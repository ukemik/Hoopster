package app.hoopster.android

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.KeyEvent
import android.view.WindowInsets
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast
import android.window.OnBackInvokedDispatcher

/**
 * The Hoopster app (task 2026-11-09_android_app, REQ-085): the Hoopster site full screen in a
 * WebView, plus what a web page cannot do on its own.
 *
 * - Volume keys: while the open screen has a mapping (the page reports it through
 *   HoopsterApp.setButtons), a short press of volume up / down calls the page's
 *   window.hoopsterButton("up" / "down") and holding either key for LONG_PRESS_MS calls
 *   window.hoopsterButton("long"). Each press gives a short tick, or a triple tick when the
 *   screen had nothing to press. On every other screen the keys change the volume as usual.
 * - Keep the screen on: the page calls HoopsterApp.setKeepAwake (practice screens and live
 *   game entry, when the Settings tick is on).
 * - Buzz patterns: the page calls HoopsterApp.buzz("target" / "finished" / "timer").
 *
 * The mapping of keys to buttons lives in the site (assets/hoopster_app.js), so it changes
 * with a site deploy, not an app update. Only the Hoopster site is loaded here: any other
 * address opens in the phone's browser, so the bridge is never offered to another site.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var siteHost: String
    private val handler = Handler(Looper.getMainLooper())

    /** Whether the open screen maps the volume keys (set by the page, on a binder thread). */
    @Volatile
    private var buttonsActive = false

    /** The volume key being held, or 0; its release is kept even if the screen changed. */
    private var heldKey = 0
    private var longFired = false
    private val longPress = Runnable {
        longFired = true
        sendButton(ACTION_LONG)
    }

    private var fileCallback: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val siteUrl = getString(R.string.site_url)
        siteHost = Uri.parse(siteUrl).host ?: ""

        web = WebView(this)
        // The WebView sits in a plain container that takes the system-bar padding: a
        // WebView does not lay its page out inside its own padding (review_1 F5).
        val root = FrameLayout(this)
        root.addView(
            web,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ),
        )
        setContentView(root)
        applySystemBarPadding(root)

        CookieManager.getInstance().setAcceptCookie(true)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.userAgentString = web.settings.userAgentString + " " + USER_AGENT_SUFFIX
        web.addJavascriptInterface(Bridge(), BRIDGE_NAME)
        web.webViewClient = SiteClient()
        web.webChromeClient = FileChooserClient()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(
                OnBackInvokedDispatcher.PRIORITY_DEFAULT
            ) { goBackOrFinish() }
        }

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(siteUrl)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        releaseHeldKey()
        CookieManager.getInstance().flush()
    }

    override fun onResume() {
        super.onResume()
        if (needsSetup) {
            askForSetupLink()
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus) {
            releaseHeldKey() // the key-up may go to another window (review_1 F7)
        }
    }

    /** Forgets a held volume key whose release this window will not see. */
    private fun releaseHeldKey() {
        handler.removeCallbacks(longPress)
        heldKey = 0
        longFired = false
    }

    override fun onDestroy() {
        handler.removeCallbacks(longPress)
        web.destroy()
        super.onDestroy()
    }

    @Deprecated("Used below Android 13 only; newer versions use the OnBackInvokedCallback.")
    override fun onBackPressed() {
        goBackOrFinish()
    }

    private fun goBackOrFinish() {
        if (web.canGoBack()) web.goBack() else finish()
    }

    // --- Volume keys ---

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (code != KeyEvent.KEYCODE_VOLUME_UP && code != KeyEvent.KEYCODE_VOLUME_DOWN) {
            return super.dispatchKeyEvent(event)
        }
        when (event.action) {
            KeyEvent.ACTION_DOWN -> {
                if (heldKey == code) {
                    return true // auto-repeat of a key we already hold
                }
                if (!buttonsActive || heldKey != 0) {
                    return super.dispatchKeyEvent(event)
                }
                heldKey = code
                longFired = false
                handler.postDelayed(longPress, LONG_PRESS_MS)
                return true
            }
            KeyEvent.ACTION_UP -> {
                if (heldKey != code) {
                    return super.dispatchKeyEvent(event)
                }
                handler.removeCallbacks(longPress)
                heldKey = 0
                if (!longFired && !event.isCanceled) {
                    sendButton(if (code == KeyEvent.KEYCODE_VOLUME_UP) ACTION_UP else ACTION_DOWN)
                }
                longFired = false
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private fun sendButton(action: String) {
        val script = "(function(){try{return !!(window.hoopsterButton&&window.hoopsterButton('" +
            action + "'));}catch(e){return false;}})()"
        web.evaluateJavascript(script) { result ->
            buzz(if (result == "true") PATTERN_TICK else PATTERN_NOTHING)
        }
    }

    // --- First use on this phone: the one-time setup link ---

    /** The site answered 404 for the last page: this phone still has to be added. */
    private var needsSetup = false
    /** The main-frame address that answered 404 in the load now finishing, or null. */
    private var notFoundUrl: String? = null
    private var setupDialog: AlertDialog? = null

    private fun askForSetupLink() {
        if (setupDialog?.isShowing == true || isFinishing) {
            return
        }
        val input = EditText(this)
        input.hint = getString(R.string.setup_hint)
        input.isSingleLine = true
        setupDialog = AlertDialog.Builder(this)
            .setTitle(R.string.setup_title)
            .setMessage(R.string.setup_message)
            .setView(input)
            .setPositiveButton(R.string.setup_open) { _, _ ->
                val target = setupAddress(input.text.toString())
                if (target != null) {
                    web.loadUrl(target)
                } else {
                    // Not a Hoopster setup link: say so and ask again once this dialog
                    // has closed (review_1 F4).
                    Toast.makeText(this, R.string.setup_refused, Toast.LENGTH_LONG).show()
                    handler.postDelayed({ askForSetupLink() }, REASK_DELAY_MS)
                }
            }
            .setNegativeButton(R.string.setup_cancel) { _, _ ->
                // The dialog comes back the next time the app is opened (review_1 F8).
                Toast.makeText(this, R.string.setup_later, Toast.LENGTH_LONG).show()
            }
            .show()
    }

    /**
     * The address to open for what was typed or pasted: a link on the Hoopster site whose
     * path starts with /enroll/ or /join/, or a bare code (opened as /enroll/<code>).
     * Anything else gives null.
     */
    private fun setupAddress(typed: String): String? {
        val text = typed.trim()
        if (text.isEmpty()) {
            return null
        }
        if (text.startsWith("https://")) {
            val uri = Uri.parse(text)
            val path = uri.path ?: ""
            val allowed = SETUP_PATH_PREFIXES.any { path.startsWith(it) }
            return if (uri.host == siteHost && allowed) uri.toString() else null
        }
        if (!SETUP_CODE_PATTERN.matches(text)) {
            return null
        }
        return getString(R.string.site_url).trimEnd('/') + "/enroll/" + Uri.encode(text)
    }

    // --- Vibration ---

    private fun vibrator(): Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            getSystemService(VibratorManager::class.java)?.defaultVibrator
        } else {
            getSystemService(Vibrator::class.java)
        }

    private fun buzz(pattern: LongArray) {
        val device = vibrator() ?: return
        if (device.hasVibrator()) {
            device.vibrate(VibrationEffect.createWaveform(pattern, -1))
        }
    }

    // --- Layout: keep the page clear of the status bar, navigation bar and keyboard ---

    private fun applySystemBarPadding(root: FrameLayout) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.VANILLA_ICE_CREAM) {
            // Below Android 15 the window is laid out around the bars, and the keyboard
            // resizes it (windowSoftInputMode adjustResize in the manifest).
            return
        }
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsets.CONSUMED
        }
    }

    // --- The page's side of the bridge: window.HoopsterApp ---

    private inner class Bridge {
        @JavascriptInterface
        fun setButtons(active: Boolean) {
            buttonsActive = active
        }

        @JavascriptInterface
        fun setKeepAwake(on: Boolean) {
            runOnUiThread {
                if (on) {
                    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                } else {
                    window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                }
            }
        }

        @JavascriptInterface
        fun buzz(kind: String) {
            val pattern = when (kind) {
                "target" -> PATTERN_TARGET
                "finished" -> PATTERN_FINISHED
                "timer" -> PATTERN_TIMER
                else -> return
            }
            this@MainActivity.buzz(pattern)
        }
    }

    private inner class SiteClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            if (uri.host == siteHost) {
                return false
            }
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (ignored: ActivityNotFoundException) {
                // No app can open it; stay on the page.
            }
            return true
        }

        /**
         * A device the site does not know yet gets a plain 404 for every page. The app has
         * no address bar, so it asks for the one-time setup link (Settings > Add a device,
         * or a sign-up link) instead.
         */
        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            if (request.isForMainFrame && errorResponse.statusCode == HTTP_NOT_FOUND &&
                request.url.host == siteHost
            ) {
                notFoundUrl = request.url.toString()
                needsSetup = true
                askForSetupLink()
            }
        }

        /**
         * Decides per load whether the phone still has to be added: WebView may report the
         * 404 before or after onPageStarted, so the flag is settled when the page finishes
         * (review_2 R2-F3).
         */
        override fun onPageFinished(view: WebView, url: String?) {
            needsSetup = url != null && url == notFoundUrl
            notFoundUrl = null // used once: a later good load of the same address clears it
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
            // A new page: it reports its own buttons and screen-on again once loaded.
            buttonsActive = false
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    /** Lets the site's file pickers (Import drills) open the phone's file chooser. */
    private inner class FileChooserClient : WebChromeClient() {
        override fun onShowFileChooser(
            webView: WebView,
            filePathCallback: ValueCallback<Array<Uri>>,
            fileChooserParams: FileChooserParams,
        ): Boolean {
            fileCallback?.onReceiveValue(emptyArray()) // an earlier chooser never answered
            fileCallback = filePathCallback
            return try {
                @Suppress("DEPRECATION")
                startActivityForResult(fileChooserParams.createIntent(), FILE_REQUEST)
                true
            } catch (ignored: ActivityNotFoundException) {
                fileCallback = null
                false
            }
        }
    }

    @Deprecated("Activity result API of the platform; AndroidX is not used.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == FILE_REQUEST) {
            val picked = WebChromeClient.FileChooserParams.parseResult(resultCode, data)
            fileCallback?.onReceiveValue(picked ?: emptyArray()) // empty = cancelled
            fileCallback = null
            return
        }
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
    }

    private companion object {
        const val BRIDGE_NAME = "HoopsterApp"
        const val USER_AGENT_SUFFIX = "HoopsterApp/1"
        const val ACTION_UP = "up"
        const val ACTION_DOWN = "down"
        const val ACTION_LONG = "long"
        const val LONG_PRESS_MS = 500L
        const val FILE_REQUEST = 7
        const val HTTP_NOT_FOUND = 404
        const val REASK_DELAY_MS = 400L
        val SETUP_PATH_PREFIXES = listOf("/enroll/", "/join/")
        val SETUP_CODE_PATTERN = Regex("[A-Za-z0-9_-]{4,128}")

        // Waveforms: off, on, off, on ... in milliseconds.
        val PATTERN_TICK = longArrayOf(0, 40)
        val PATTERN_NOTHING = longArrayOf(0, 20, 70, 20, 70, 20)
        val PATTERN_TARGET = longArrayOf(0, 120, 100, 120)
        val PATTERN_FINISHED = longArrayOf(0, 400, 150, 150, 150, 400)
        val PATTERN_TIMER = longArrayOf(0, 800)
    }
}
