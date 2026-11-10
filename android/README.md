# Hoopster Android app

A small Android app that shows the Hoopster site (https://hoopster-nend.onrender.com) full
screen. It adds what a web page cannot do:

- **Volume buttons** on the practice screens. The key mapping is described in
  `assets/hoopster_app.js`.
- **Keep the screen on** during a practice. This follows the Settings → This device tick.
- **Buzz patterns:** target reached, drill finished, timer ended.

Task `2026-11-09_android_app`, REQ-085. This folder is completely separate from the web app.
No Python code uses it, and Render never builds it. The only links between the two are the site
address (`app/src/main/res/values/strings.xml`) and the bridge the site offers in
`assets/hoopster_app.js`.

**Nothing is installed on the PC.** GitHub builds the app on its own servers with the two
workflows in `.github/workflows/`:

| Workflow | When it runs | What you get |
|---|---|---|
| `android-build.yml` ("Android app") | Every push to `main` that changes `android/` or that file, or by hand | The APK as an artifact `hoopster-app-<n>`, kept for 30 days |
| `android-signing-key.yml` ("Android signing key") | By hand, **once** | The signing key and its passwords as an artifact, kept for 1 day |

## One-time setup

1. **Push** this folder and the two workflow files. The first "Android app" run fails with
   "The signing secrets are missing". That's expected.
2. On GitHub, go to **Actions**, open **Android signing key**, click **Run workflow**, and wait
   about a minute.
3. Open the finished run and download the artifact **hoopster-signing-key** (a zip). Unzip it.
4. On GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**.
   Add four secrets. Each secret's **name** is the file name without `.txt`, and its **value**
   is the file's whole content:
   - `HOOPSTER_KEYSTORE_B64`
   - `HOOPSTER_KEYSTORE_PASSWORD`
   - `HOOPSTER_KEY_ALIAS`
   - `HOOPSTER_KEY_PASSWORD`
5. Keep `hoopster-key-backup.jks` and the password file somewhere safe and private, such as a
   password manager. Then **delete the artifact** on the run page and delete the unzipped files.
   Without this key, an app update can't be installed over the old app.
6. Go to **Actions → Android app → Run workflow**.

## Installing on the phone

1. On the phone, open the repository on github.com, signed in.
2. Go to **Actions → Android app**, open the latest green run, and download
   **hoopster-app-<n>**.
3. Open the downloaded zip and tap `hoopster-<n>.apk`. Android asks once to allow installs from
   your browser or file app ("Install unknown apps"). Allow it, then tap **Install**.
4. Open **Hoopster**. The app keeps its own login, separate from Chrome, so the site doesn't
   know it yet, and the app asks **"Add this phone"**.
   - In **Chrome on the same phone** (already set up), open Settings → Add a device, make a
     link and copy it.
   - Switch back to the app, paste the link (or just the code), and tap **Open**. Then sign in.

Updating works the same way: install the newer APK over the old one, and the login stays.

## Using it

The volume buttons only act on screens that have a mapping, and only while the app is open and
the screen is on. Everywhere else they change the volume as usual.

| Screen | Volume up | Volume down | Hold either (~0.5 s) |
|---|---|---|---|
| Court: Make / Miss | Make | Miss | Undo |
| Court with **Free throws** ticked | Make (free throw) | Miss (free throw) | Undo |
| Outcome drill with one button group | its success button | its other button | Undo |
| Count drill (reps, errors, streak) | +1 (if a countdown hasn't started: Start) | −1 | Save attempt |
| Stopwatch drill | Start | Stop | Reset |

**Buzzes:**

| You feel | It means |
|---|---|
| one short tick | the key press was entered |
| three tiny ticks | nothing to press, e.g. no court spot chosen yet |
| two medium buzzes | target reached for this spot |
| long, two short, long | drill finished, all spots done |
| one long buzz | timer ended |

**Not in the app:** **Export data** downloads don't work inside the app; use Chrome for them.

## Render

A push that only changes `android/` or `.github/` also makes Render redeploy the site. That's
harmless. If you'd rather skip it, add `android/**` and `.github/**` under **Build Filters →
Ignored paths** in the Render service settings.
