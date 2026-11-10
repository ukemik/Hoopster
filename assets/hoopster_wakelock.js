/*
 * Keep the screen on during a practice (task 2026-11-09_keep_awake, REQ-084).
 * Clientside callback functions, namespace "hoopster_wake", registered with
 * ClientsideFunction("hoopster_wake", "<name>") by src/app_controller.py (keep_awake) and
 * src/controller_settings.py (setting_load, setting_save).
 *
 * Unlike assets/hoopster.js these functions have side effects: they hold or release the
 * browser's screen wake lock and read or write one localStorage key. The setting is per
 * device (this browser), not per account, so there is no schema change.
 *
 * The wake lock needs a secure context (HTTPS, or http://127.0.0.1 / localhost) and a
 * visible page. Where the browser has no Wake Lock API, or refuses the request (battery
 * saver, plain http on a LAN address), nothing happens and the page works as before.
 * The browser drops the lock when the page is hidden; it is taken again when the page
 * becomes visible while a practice screen is open.
 */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  hoopster_wake: (function () {
    "use strict";

    // localStorage key and its two values; a missing or unreadable key means "on".
    var SETTING_KEY = "hoopster.keepAwake";
    var SETTING_ON = "on";
    var SETTING_OFF = "off";

    // The practice screens (overview, court, run, run one drill) and live game entry.
    // Duplicated from the route patterns in src/app_controller.py PATTERN_ROUTES.
    var AWAKE_PATH_PATTERNS = [
      /^\/practice\/\d+$/,
      /^\/practice\/\d+\/court$/,
      /^\/practice\/\d+\/run$/,
      /^\/practice\/\d+\/run\/\d+$/,
      /^\/games\/\d+\/live$/,
    ];

    // "wanted" means the lock was asked for, not that the browser granted it.
    var STATE_WANTED = "wanted";
    var STATE_RELEASED = "released";
    var STATE_UNSUPPORTED = "unsupported";

    var sentinel = null; // the WakeLockSentinel while the lock is held
    var wanted = false; // whether the current screen should keep the phone awake
    var requesting = false; // a request is in flight; never send two at once

    function isSupported() {
      return typeof navigator !== "undefined" && !!navigator.wakeLock &&
        typeof navigator.wakeLock.request === "function";
    }

    function readSetting() {
      try {
        return window.localStorage.getItem(SETTING_KEY) !== SETTING_OFF;
      } catch (err) {
        return true;
      }
    }

    function writeSetting(on) {
      try {
        window.localStorage.setItem(SETTING_KEY, on ? SETTING_ON : SETTING_OFF);
      } catch (err) {
        // Storage blocked (private window): the switch still works for this page view.
      }
    }

    function isAwakePath(pathname) {
      var path = pathname || "";
      for (var i = 0; i < AWAKE_PATH_PATTERNS.length; i++) {
        if (AWAKE_PATH_PATTERNS[i].test(path)) {
          return true;
        }
      }
      return false;
    }

    function acquire() {
      if (!wanted || sentinel !== null || requesting || !isSupported() ||
          document.visibilityState !== "visible") {
        return;
      }
      requesting = true;
      navigator.wakeLock.request("screen").then(function (lock) {
        requesting = false;
        if (!wanted) {
          // The screen changed while the request was in flight.
          lock.release().catch(function () {});
          return;
        }
        sentinel = lock;
        lock.addEventListener("release", function () {
          if (sentinel === lock) {
            sentinel = null;
          }
        });
      }).catch(function () {
        // Refused (battery saver, insecure address, page hidden): keep working without it.
        requesting = false;
      });
    }

    function release() {
      if (sentinel !== null) {
        var lock = sentinel;
        sentinel = null;
        lock.release().catch(function () {});
      }
    }

    // Inside the Android app (task 2026-11-09_android_app) the app keeps the screen on
    // itself, because a WebView may have no Wake Lock API.
    function tellApp(on) {
      try {
        var bridge = window.HoopsterApp;
        if (bridge && typeof bridge.setKeepAwake === "function") {
          bridge.setKeepAwake(on);
        }
      } catch (err) {
        // The app is optional; the page works without it.
      }
    }

    function apply(pathname) {
      wanted = readSetting() && isAwakePath(pathname);
      tellApp(wanted);
      if (!isSupported()) {
        return;
      }
      if (wanted) {
        acquire();
      } else {
        release();
      }
    }

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") {
          acquire();
        }
      });
    }

    return {
      // On every navigation: hold the lock on a practice screen when the setting is on,
      // release it anywhere else. Returns a short state for the keep-awake-state store:
      // "wanted" (asked for; the browser may still refuse), "released" or "unsupported".
      keep_awake: function (pathname) {
        apply(pathname);
        if (!isSupported()) {
          return STATE_UNSUPPORTED;
        }
        return wanted ? STATE_WANTED : STATE_RELEASED;
      },

      // Settings hub: shows this device's stored setting when the checkbox appears, and
      // marks the checkbox ready so later changes are stored.
      setting_load: function (elementId) {
        return [readSetting(), true];
      },

      // Settings hub: stores the checkbox for this device once it shows the stored value
      // (ready). Returns the stored value, or no update before that.
      setting_save: function (checked, ready) {
        if (ready !== true) {
          return window.dash_clientside.no_update;
        }
        var on = checked !== false;
        writeSetting(on);
        if (!on) {
          wanted = false;
          tellApp(false);
          release();
        }
        return on ? SETTING_ON : SETTING_OFF;
      },
    };
  })(),
});
