/*
 * Bridge to the Hoopster Android app (task 2026-11-09_android_app, REQ-085).
 *
 * The app (android/ folder) shows this site in a WebView and adds the JavaScript object
 * window.HoopsterApp with setButtons(boolean), setKeepAwake(boolean) and buzz(kind). In an
 * ordinary browser window.HoopsterApp does not exist and this file only defines the two
 * functions below, which then do nothing.
 *
 * window.hoopsterButton(action) - called by the app for a volume key: action "up", "down"
 *   or "long" (either key held). Presses the button the current screen maps it to and
 *   returns true, or returns false when the screen has no usable target (the app then gives
 *   a "nothing happened" buzz). Mapping (approved by the Overseer 2026-11-09):
 *     court, Make/Miss panel shown ... up Make, down Miss, long Undo (with the Free throws
 *                                      box ticked, Make / Miss enter free throws)
 *     outcome drill with one group ... up its first success button, down its first other
 *                                      button, long Undo (several groups: not mapped)
 *     count drill (reps, errors,
 *       streak) ...................... up +1 (with a countdown not yet started: Start),
 *                                      down -1, long Save attempt
 *     stopwatch drill ................ up Start, down Stop, long Reset
 *   Live game entry (/games/<id>/live, which has the same court panel), every other
 *   screen, and any screen while a dialog or drawer is open are not mapped; the keys then
 *   change the volume.
 * window.hoopsterBuzz(kind) - asks the app to vibrate "target", "finished" or "timer";
 *   returns false outside the app so the caller can fall back to navigator.vibrate.
 *
 * Inside the app a MutationObserver tells the app whether the current screen has a target
 * (HoopsterApp.setButtons), because the app must decide at once whether to keep a volume
 * key or let it change the volume.
 *
 * Element ids and classes are duplicated from src/layouts_entry.py (court-make, court-miss,
 * court-undo), src/layouts_run.py (OUTCOME_GRID_CLASS,
 * OUTCOME_SUCCESS_CLASS, OUTCOME_FAIL_CLASS, outcome-undo, drill-result-type, drill-value-1,
 * drill-add-attempt, drill-timer-*; STOPWATCH_TYPES, COUNTDOWN_TYPES) and the result type
 * codes of sql/h_ref.sql.
 */
(function () {
  "use strict";

  var COUNT_TYPES = { fixed_time_reps: true, fixed_time_errors: true, failure_streak: true };
  var STOPWATCH_TYPES = { fixed_task_time: true, failure_time: true };
  var OUTCOME_GRID_SELECTOR = ".hoopster-outcome-grid";
  var OUTCOME_SUCCESS_SELECTOR = ".hoopster-outcome-success";
  var OUTCOME_FAIL_SELECTOR = ".hoopster-outcome-fail";
  var TIMER_DONE_TEXT = "Time!";
  // Set by src/layouts_run.py _timer_controls on drill-timer-area.
  var INITIAL_ATTRIBUTE = "data-timer-initial";
  var OBSERVE_DELAY_MS = 150;
  // Live game entry shares the court panel but is not mapped (approved table).
  var LIVE_GAME_PATH = /^\/games\/\d+\/live$/;
  // An open Mantine modal or drawer covers the screen; its buttons are not mapped.
  var OVERLAY_SELECTOR = ".mantine-Modal-content, .mantine-Drawer-content";

  function app() {
    return window.HoopsterApp || null;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  // Shown on screen: in the layout and not inside a hidden panel.
  function isShown(element) {
    return !!element && element.getClientRects().length > 0;
  }

  function isUsable(element) {
    return isShown(element) && !element.disabled &&
      element.getAttribute("data-disabled") !== "true";
  }

  function press(element) {
    if (!isUsable(element)) {
      return false;
    }
    element.click();
    return true;
  }

  // The <input> of a dmc.NumberInput: the id is on the input itself or on a wrapper.
  function numberInput(id) {
    var element = byId(id);
    if (element && element.tagName !== "INPUT") {
      element = element.querySelector("input");
    }
    return element;
  }

  // Sets a React-controlled input's value so its onChange (and Dash's value prop) follow.
  function setInputValue(input, text) {
    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function stepCount(delta) {
    var input = numberInput("drill-value-1");
    if (!isShown(input) || input.disabled) {
      return false;
    }
    var current = parseInt(input.value, 10);
    var next = Math.max(0, (isNaN(current) ? 0 : current) + delta);
    setInputValue(input, String(next));
    return true;
  }

  // A countdown that has not run yet: the display still shows its starting text, which the
  // layout puts on drill-timer-area (so it is right for every drill, review_1 F3).
  function countdownIdle() {
    var display = byId("drill-timer-display");
    var area = byId("drill-timer-area");
    if (!display || !area) {
      return false;
    }
    var text = display.textContent;
    return text !== TIMER_DONE_TEXT && text === area.getAttribute(INITIAL_ATTRIBUTE);
  }

  function overlayOpen() {
    return Array.prototype.some.call(document.querySelectorAll(OVERLAY_SELECTOR), isShown);
  }

  function shownOutcomeGrids() {
    return Array.prototype.filter.call(document.querySelectorAll(OUTCOME_GRID_SELECTOR), isShown);
  }

  // The current screen's {up, down, long} handlers, or null when it is not mapped.
  function targets() {
    if (LIVE_GAME_PATH.test(window.location.pathname) || overlayOpen()) {
      return null;
    }
    if (isShown(byId("court-make"))) {
      return {
        up: function () { return press(byId("court-make")); },
        down: function () { return press(byId("court-miss")); },
        long: function () { return press(byId("court-undo")); },
      };
    }
    var grids = shownOutcomeGrids();
    if (grids.length === 1) {
      var success = grids[0].querySelector(OUTCOME_SUCCESS_SELECTOR);
      var fail = grids[0].querySelector(OUTCOME_FAIL_SELECTOR);
      if (success && fail) {
        return {
          up: function () { return press(success); },
          down: function () { return press(fail); },
          long: function () { return press(byId("outcome-undo")); },
        };
      }
      return null;
    }
    var marker = byId("drill-result-type");
    var type = marker ? marker.getAttribute("data-result-type") : null;
    if (type && COUNT_TYPES[type]) {
      return {
        up: function () {
          var start = byId("drill-timer-start");
          if (start && countdownIdle()) {
            return press(start);
          }
          return stepCount(1);
        },
        down: function () { return stepCount(-1); },
        long: function () { return press(byId("drill-add-attempt")); },
      };
    }
    if (type && STOPWATCH_TYPES[type] && byId("drill-timer-start")) {
      return {
        up: function () { return press(byId("drill-timer-start")); },
        down: function () { return press(byId("drill-timer-stop")); },
        long: function () { return press(byId("drill-timer-reset")); },
      };
    }
    return null;
  }

  window.hoopsterButton = function (action) {
    var mapped = targets();
    if (!mapped || typeof mapped[action] !== "function") {
      return false;
    }
    try {
      return mapped[action]() === true;
    } catch (err) {
      return false;
    }
  };

  window.hoopsterBuzz = function (kind) {
    var bridge = app();
    if (!bridge || typeof bridge.buzz !== "function") {
      return false;
    }
    try {
      bridge.buzz(String(kind));
      return true;
    } catch (err) {
      return false;
    }
  };

  // Inside the app only: report after every page change whether the volume keys are mapped.
  if (app() && typeof MutationObserver === "function") {
    var pending = null;
    var lastReported = null;
    var report = function () {
      pending = null;
      var active = targets() !== null;
      if (active !== lastReported) {
        lastReported = active;
        try {
          app().setButtons(active);
        } catch (err) {
          lastReported = null;
        }
      }
    };
    var schedule = function () {
      if (pending === null) {
        pending = window.setTimeout(report, OBSERVE_DELAY_MS);
      }
    };
    var start = function () {
      new MutationObserver(schedule).observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class", "style", "disabled"],
      });
      schedule();
    };
    if (document.body) {
      start();
    } else {
      document.addEventListener("DOMContentLoaded", start);
    }
  }
})();
