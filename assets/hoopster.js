/*
 * Hoopster clientside callback functions, namespace "hoopster" (plan §3.5, §3.6, item 15;
 * task 2026-10-23_shot_entry_complete item 7).
 * Registered by the controllers with ClientsideFunction("hoopster", "<name>").
 *
 * Every function is pure apart from Date.now() / new Date() and makes no network call.
 * Exactly one function, court_dispatch, writes the court command stores (court-queue,
 * court-confirmed, court-outbox, court-failure), so JavaScript's single thread rules out lost
 * updates. court-ack is written by the server through set_props, which starts a fresh Dash
 * callback chain, so the dispatcher may listen to it without forming a loop. Give no callback
 * an Output on court-ack. court-request is written only by entry_form_submit, which hands a
 * validated X of Y, No spot or free-throw totals entry to the dispatcher.
 *
 * Drill mode (task 2026-10-20_practice_drills, plan §3.6-3.7): when entry-parent.kind is
 * "drill", court_dispatch adds side and new_attempt_after to every insert, court_render and
 * drill_progress start a player's tally from 0 while a New attempt request is pending, and
 * select_spot auto-advances through court-plan. With court-plan null (free court, games) the
 * new triggers return no_update, so those screens behave as before. new_attempt_request is
 * the single writer of court-attempt-request; timer_step is the single writer of drill-timer,
 * drill-timer-display, drill-timer-tick.disabled and (stopwatch Stop) drill-value-1;
 * outcome_pick is the single writer of outcome-pending and outcome-submit.
 */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  hoopster: (function () {
    "use strict";

    // Duplicated from src/layouts_entry.py SPOT_COLOR and SPOT_SELECTED_COLOR.
    var SPOT_COLOR = "#495057";
    var SPOT_SELECTED_COLOR = "#fd7e14";
    // Duplicated from config/app_config.py COURT_SEND_TIMEOUT_MS,
    // COURT_AUTO_RETRY_INTERVAL_MS, COURT_AUTO_RETRY_MAX and ENTRY_ATTEMPTS_MAX.
    var COURT_SEND_TIMEOUT_MS = 10000;
    var COURT_AUTO_RETRY_INTERVAL_MS = 5000;
    var COURT_AUTO_RETRY_MAX = 12;
    var ENTRY_ATTEMPTS_MAX = 500;

    // Duplicated from src/layouts_entry.py (COURT_CLASS, the full-court class, HIDDEN_CLASS,
    // DETAIL_NONE and the hand, creation and defense labels).
    var COURT_CLASS = "hoopster-court";
    var COURT_FULL_CLASS = "hoopster-court hoopster-court-full";
    var HIDDEN_CLASS = "hoopster-hidden";
    var DETAIL_NONE = "none";
    var DETAIL_LABELS = {
      left: "Left",
      right: "Right",
      catch_and_shoot: "Catch & shoot",
      off_dribble: "Off dribble",
      open: "Open",
      contested: "Contested",
    };
    var DETAILS_EMPTY_SUMMARY = "No details";

    var TABLE_SHOT = "shot";
    var TABLE_FREE_THROW = "free_throw";
    var MODE_SINGLE = "single";
    var MODE_CUMULATIVE = "cumulative";
    var MODE_MANUAL = "manual";
    var VIEW_FULL = "full";

    var MESSAGE_PICK_PLAYER = "Pick a player first.";
    var MESSAGE_TAP_SPOT = "Tap a spot on the court first.";
    var MESSAGE_COUNTS =
      "Enter attempts from 1 to " + ENTRY_ATTEMPTS_MAX + " and makes from 0 to attempts.";

    var KEY_SUFFIX_LENGTH = 6;
    var KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
    var DEFAULT_ZONE = "UTC";
    var COMMAND_STORE_COUNT = 4;
    var SELECT_SPOT_OUTPUT_COUNT = 6;
    var FORM_OUTPUT_COUNT = 8;
    var DETAIL_COUNT = 4;

    // Mantine colour names for the save-status badge (REQ-058).
    var STATUS_READY = { text: "Ready", color: "gray" };
    var STATUS_SAVING = { text: "Saving...", color: "blue" };
    var STATUS_FAILED_COLOR = "red";
    var STATUS_SAVED_COLOR = "green";
    var RETRY_SHOWN_STYLE = {};
    var RETRY_HIDDEN_STYLE = { display: "none" };

    // Drill mode (plan §3.6-3.7). Duplicated from src/data_entry.py PARENT_KIND_DRILL and
    // src/layouts_entry.py NO_PERSONAL_BEST.
    var PARENT_KIND_DRILL = "drill";
    var NO_PERSONAL_BEST = "No personal best yet";
    var ALL_SPOTS_DONE = "All spots done";
    var FREE_THROW_KEY = "ft";
    var SPOT_KEY_PREFIX = "s";
    var CURRENT_SPOT_MARK = " ◀";
    // Timers (REQ-056, D17); modes duplicated from src/layouts_run.py.
    var TIMER_MODE_STOPWATCH = "stopwatch";
    var TIMER_MODE_COUNTDOWN = "countdown";
    var TIMER_DONE_TEXT = "Time!";
    var TIMER_VIBRATE_MS = 200;
    var MS_PER_SECOND = 1000;
    var SECONDS_PER_MINUTE = 60;
    var TIMER_OUTPUT_COUNT = 4;
    var OUTCOME_OUTPUT_COUNT = 3;

    // --- Drill-mode helpers (plan §3.7) ---

    // Whether a player has a pending New attempt request: the request equals the latest
    // attempt number in court-base, so the next shot starts the attempt after it.
    function isPending(base, request, playerId) {
      var key = String(playerId);
      var playerBase = base && base[key];
      return !!request && request[key] !== undefined && request[key] !== null &&
        !!playerBase && playerBase.attempt_no !== undefined &&
        request[key] === playerBase.attempt_no;
    }

    // court-base with one player's attempt totals set to 0 (pending request).
    function baseFromZero(base, playerId) {
      var copy = Object.assign({}, base || {});
      copy[String(playerId)] = { makes: 0, attempts: 0, ft_makes: 0, ft_attempts: 0 };
      return copy;
    }

    // The spot key of a command or confirmed entry: "ft" or "s<court_spot_id>".
    function spotKeyOf(entry) {
      if (entry.spot_key) {
        return entry.spot_key;
      }
      return entry.table === TABLE_FREE_THROW ? FREE_THROW_KEY : SPOT_KEY_PREFIX + entry.court_spot_id;
    }

    // Attempts per spot key for one player in the attempt in progress: the base's spots
    // (none while a request is pending), plus queued inserts, minus confirmed entries a
    // queued delete targets.
    function spotCounts(base, queue, confirmed, playerId, pending) {
      var playerBase = (base && base[String(playerId)]) || {};
      var counts = Object.assign({}, pending ? {} : (playerBase.spots || {}));
      var deletedKeys = {};
      var i;
      for (i = 0; i < queue.length; i++) {
        if (queue[i].op === "delete") {
          deletedKeys[queue[i].key] = true;
        }
      }
      for (i = 0; i < queue.length; i++) {
        var command = queue[i];
        if (command.op === "insert" && command.player_id === playerId && !deletedKeys[command.key]) {
          var key = spotKeyOf(command);
          counts[key] = (counts[key] || 0) + (command.attempts || 0);
        }
      }
      if (!pending) {
        for (i = 0; i < confirmed.length; i++) {
          var entry = confirmed[i];
          if (deletedKeys[entry.key] && entry.player_id === playerId && entry.spot_key) {
            counts[entry.spot_key] = Math.max(0, (counts[entry.spot_key] || 0) - (entry.attempts || 0));
          }
        }
      }
      return counts;
    }

    // The court spots of a plan (the free-throw line is never selected on the court).
    function planCourtSpots(plan) {
      return ((plan && plan.spots) || []).filter(function (spot) {
        return spot.court_spot_id !== null && spot.court_spot_id !== undefined;
      });
    }

    function belowTarget(spot, counts) {
      return spot.target === null || spot.target === undefined ||
        (counts[spot.key] || 0) < spot.target;
    }

    // Adds a drill command's side and new_attempt_after (plan §3.7 "Attempts"); other
    // parents get nothing, so their commands are unchanged.
    function withDrillFields(command, parent, request, side, base) {
      if (!parent || parent.kind !== PARENT_KIND_DRILL) {
        return command;
      }
      command.side = side === "left" || side === "right" ? side : null;
      command.new_attempt_after = isPending(base, request, command.player_id)
        ? request[String(command.player_id)]
        : null;
      return command;
    }

    function timerElapsed(timer, now) {
      var running = timer.running && timer.started_at ? now - timer.started_at : 0;
      return (timer.elapsed_ms || 0) + running;
    }

    // "12.34 s" (stopwatch) or "m:ss" remaining (countdown).
    function timerDisplay(timer, now) {
      var elapsed = timerElapsed(timer, now);
      if (timer.mode === TIMER_MODE_COUNTDOWN) {
        var remaining = Math.max(0, (timer.limit_s || 0) * MS_PER_SECOND - elapsed);
        var seconds = Math.ceil(remaining / MS_PER_SECOND);
        return Math.floor(seconds / SECONDS_PER_MINUTE) + ":" + pad2(seconds % SECONDS_PER_MINUTE);
      }
      return (elapsed / MS_PER_SECOND).toFixed(2) + " s";
    }

    // The triggering pattern id of an ALL callback as an object, or null.
    function triggeredPattern() {
      var id = triggeredId();
      if (id && typeof id === "object") {
        return id;
      }
      if (typeof id === "string" && id.charAt(0) === "{") {
        try {
          return JSON.parse(id);
        } catch (error) {
          return null;
        }
      }
      return null;
    }

    function triggeredValue() {
      var context = window.dash_clientside.callback_context;
      var triggered = context && context.triggered;
      return triggered && triggered.length ? triggered[0].value : undefined;
    }

    // --- Helpers ---

    function noUpdate() {
      return window.dash_clientside.no_update;
    }

    function noUpdates(count) {
      var result = [];
      for (var i = 0; i < count; i++) {
        result.push(noUpdate());
      }
      return result;
    }

    function triggeredId() {
      var context = window.dash_clientside.callback_context;
      return context ? context.triggered_id : undefined;
    }

    function pad2(value) {
      return (value < 10 ? "0" : "") + value;
    }

    // A command key unique within the visit: tap time plus a random suffix (not a secret).
    function newCommandKey() {
      var suffix = "";
      for (var i = 0; i < KEY_SUFFIX_LENGTH; i++) {
        suffix += KEY_ALPHABET.charAt(Math.floor(Math.random() * KEY_ALPHABET.length));
      }
      return Date.now() + "-" + suffix;
    }

    function findIndexByKey(list, key) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].key === key) {
          return i;
        }
      }
      return -1;
    }

    // The court-player value (a string, as dmc values are) as an integer player id, or NaN
    // when it is missing or not a plain whole number. NaN matches no entry.
    function parsePlayerId(value) {
      if (typeof value === "number") {
        return Number.isInteger(value) ? value : NaN;
      }
      if (typeof value === "string" && /^[0-9]+$/.test(value)) {
        return parseInt(value, 10);
      }
      return NaN;
    }

    // A number input's value (a number, or text while typing, "" when empty) as a whole
    // number, or NaN.
    function parseCount(value) {
      if (typeof value === "number") {
        return Number.isInteger(value) ? value : NaN;
      }
      if (typeof value === "string" && /^[0-9]+$/.test(value.trim())) {
        return parseInt(value.trim(), 10);
      }
      return NaN;
    }

    function countsValid(makes, attempts) {
      return (
        !isNaN(makes) && !isNaN(attempts) &&
        attempts >= 1 && attempts <= ENTRY_ATTEMPTS_MAX &&
        makes >= 0 && makes <= attempts
      );
    }

    // A detail control's value as stored: "none", empty and missing are null.
    function detailValue(value) {
      if (value === null || value === undefined || value === "" || value === DETAIL_NONE) {
        return null;
      }
      return value;
    }

    // The four detail values as a command's details, or null when none is set.
    function detailsOf(shotType, hand, creation, defense) {
      var details = {
        shot_type_code: detailValue(shotType),
        hand: detailValue(hand),
        creation: detailValue(creation),
        defense: detailValue(defense),
      };
      if (
        details.shot_type_code === null && details.hand === null &&
        details.creation === null && details.defense === null
      ) {
        return null;
      }
      return details;
    }

    function hasSpot(spot) {
      return !!spot && spot.court_spot_id !== undefined && spot.court_spot_id !== null;
    }

    // The entries of this visit that still count for one player, oldest first, both tables:
    // confirmed inserts, then queued inserts, leaving out every entry a queued delete
    // targets. Keys are unique within the visit, so the deletes need no player filter.
    function effectiveEntries(queue, confirmed, playerId) {
      var deletedKeys = {};
      var entries = [];
      var i;
      for (i = 0; i < queue.length; i++) {
        if (queue[i].op === "delete") {
          deletedKeys[queue[i].key] = true;
        }
      }
      for (i = 0; i < confirmed.length; i++) {
        if (confirmed[i].player_id === playerId && !deletedKeys[confirmed[i].key]) {
          entries.push({ key: confirmed[i].key, table: confirmed[i].table, queued: false });
        }
      }
      for (i = 0; i < queue.length; i++) {
        if (
          queue[i].op === "insert" && queue[i].player_id === playerId &&
          !deletedKeys[queue[i].key]
        ) {
          entries.push({ key: queue[i].key, table: queue[i].table, queued: true });
        }
      }
      return entries;
    }

    // Prepares the queue head for sending. The attempt number counts up per command, so a
    // late ack of an earlier attempt can never match. A delete resolves its entry_id from the
    // confirmed list; FIFO order guarantees its insert was confirmed first, and a delete with
    // no target (nothing left to undo) is dropped.
    function prepareSend(queue, confirmed) {
      var remaining = queue.slice();
      while (remaining.length) {
        var head = remaining[0];
        var attempt = (head.attempt || 0) + 1;
        var outbox = Object.assign({}, head, { attempt: attempt, sent_at: Date.now() });
        if (head.op === "delete") {
          var targetIndex = findIndexByKey(confirmed, head.key);
          if (targetIndex < 0) {
            remaining.shift();
            continue;
          }
          outbox.entry_id = confirmed[targetIndex].entry_id;
        }
        remaining[0] = Object.assign({}, head, { attempt: attempt });
        return { queue: remaining, outbox: outbox };
      }
      return { queue: remaining, outbox: null };
    }

    // Queues an undo of the player's most recent effective entry (field goal or free throw):
    // a queued insert not yet sent is cancelled; anything else gets a delete command carrying
    // the same player and table. Returns null when the player has nothing undoable.
    function applyUndo(queue, confirmed, outbox, playerId) {
      var entries = effectiveEntries(queue, confirmed, playerId);
      if (!entries.length) {
        return null;
      }
      var last = entries[entries.length - 1];
      var inFlight = outbox && outbox.key === last.key;
      if (last.queued && !inFlight) {
        return queue.filter(function (command) {
          return !(command.op === "insert" && command.key === last.key);
        });
      }
      return queue.concat([
        { op: "delete", key: last.key, player_id: playerId, table: last.table },
      ]);
    }

    // A new insert command for the queue.
    function insertCommand(playerId, table, mode, spotId, pointValue, makes, attempts, details) {
      return {
        op: "insert",
        key: newCommandKey(),
        player_id: playerId,
        table: table,
        entry_mode: mode,
        court_spot_id: spotId,
        point_value: pointValue,
        makes: makes,
        attempts: attempts,
        details: details,
        tapped_at: new Date().toISOString(),
      };
    }

    // Field-goal or free-throw totals of one player: base plus queued inserts, minus
    // confirmed entries a queued delete targets (they are already in the base).
    function playerTotals(base, queue, confirmed, playerId) {
      var playerBase = (base && base[String(playerId)]) || {};
      var totals = {
        shot: { makes: playerBase.makes || 0, attempts: playerBase.attempts || 0 },
        free_throw: { makes: playerBase.ft_makes || 0, attempts: playerBase.ft_attempts || 0 },
      };
      var deletedKeys = {};
      var i;
      for (i = 0; i < queue.length; i++) {
        var command = queue[i];
        if (command.op === "delete") {
          deletedKeys[command.key] = true;
        } else if (command.player_id === playerId && totals[command.table]) {
          totals[command.table].makes += command.makes || 0;
          totals[command.table].attempts += command.attempts || 0;
        }
      }
      for (i = 0; i < queue.length; i++) {
        if (queue[i].op === "insert" && deletedKeys[queue[i].key] &&
            queue[i].player_id === playerId && totals[queue[i].table]) {
          totals[queue[i].table].makes -= queue[i].makes || 0;
          totals[queue[i].table].attempts -= queue[i].attempts || 0;
        }
      }
      for (i = 0; i < confirmed.length; i++) {
        var entry = confirmed[i];
        if (deletedKeys[entry.key] && entry.player_id === playerId && totals[entry.table]) {
          totals[entry.table].makes -= entry.makes || 0;
          totals[entry.table].attempts -= entry.attempts || 0;
        }
      }
      return totals;
    }

    function tallyText(makes, attempts) {
      return attempts > 0
        ? makes + " / " + attempts + " (" + Math.round((makes * 100) / attempts) + "%)"
        : "0 / 0";
    }

    // court-failure: when the latest send failed and how many automatic resends were made.
    function newFailure(autoRetries) {
      return { failed_at: Date.now(), auto_retries: autoRetries };
    }

    // Deep copy of a figure with one spot marked as selected (or none).
    function recolouredFigure(figure, spotId) {
      var newFigure = JSON.parse(JSON.stringify(figure));
      var meta = newFigure.data[0].meta || {};
      var spots = meta.spots || [];
      var colors = spots.map(function (spot) {
        return spot.court_spot_id === spotId ? SPOT_SELECTED_COLOR : SPOT_COLOR;
      });
      newFigure.data[0].marker = Object.assign({}, newFigure.data[0].marker, {
        color: colors,
      });
      return newFigure;
    }

    function shotTypeLabel(code, options) {
      var list = options || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i] && list[i].value === code) {
          return list[i].label;
        }
      }
      return code;
    }

    function sameDetails(memory, shotType, hand, creation, defense) {
      return (
        detailValue(memory.shot_type_code) === detailValue(shotType) &&
        detailValue(memory.hand) === detailValue(hand) &&
        detailValue(memory.creation) === detailValue(creation) &&
        detailValue(memory.defense) === detailValue(defense)
      );
    }

    // select_spot's drill-mode auto-advance (plan §3.7). Returns the six select_spot
    // outputs. Nothing changes without a plan, without a selected plan spot (the user tapped
    // a spot outside the plan) or while the selected spot is still below its target.
    function advanceSpot(figure, selected, base, plan, playerId, queue, confirmed, request) {
      var courtSpots = planCourtSpots(plan);
      if (!courtSpots.length || isNaN(playerId) || !hasSpot(selected) || !figure ||
          !figure.data || !figure.data.length) {
        return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
      }
      var counts = spotCounts(base, queue, confirmed, playerId, isPending(base, request, playerId));
      var current = -1;
      for (var i = 0; i < courtSpots.length; i++) {
        if (courtSpots[i].court_spot_id === selected.court_spot_id) {
          current = i;
          break;
        }
      }
      if (current < 0 || belowTarget(courtSpots[current], counts)) {
        return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
      }
      var next = null;
      for (var step = 1; step < courtSpots.length && next === null; step++) {
        var candidate = courtSpots[(current + step) % courtSpots.length];
        if (belowTarget(candidate, counts)) {
          next = candidate;
        }
      }
      if (next === null) {
        var done = noUpdates(SELECT_SPOT_OUTPUT_COUNT);
        done[2] = ALL_SPOTS_DONE;
        return done;
      }
      var metaSpots = (figure.data[0].meta || {}).spots || [];
      var chosen = null;
      for (var j = 0; j < metaSpots.length; j++) {
        if (metaSpots[j].court_spot_id === next.court_spot_id) {
          chosen = metaSpots[j];
          break;
        }
      }
      if (chosen === null) {
        return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
      }
      return [
        recolouredFigure(figure, chosen.court_spot_id),
        {
          court_spot_id: chosen.court_spot_id,
          spot_number: chosen.spot_number,
          spot_name: chosen.spot_name,
          zone_label: chosen.zone_label,
        },
        "#" + chosen.spot_number + " " + chosen.spot_name + " · " + chosen.zone_label,
        false,
        false,
        noUpdate(),
      ];
    }

    return {
      // --- Shell: device clock and drawers (plan §3.5) ---

      // Writes the device's own date and IANA zone (REQ-055); never the server's clock.
      device_clock: function (pathname) {
        var now = new Date();
        var today =
          now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());
        var zone = Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_ZONE;
        return { today: today, zone: zone };
      },

      open_drawer: function (nClicks) {
        return nClicks ? true : noUpdate();
      },

      // Closes a dialog (e.g. a Cancel button's Output on a dmc.Modal's opened).
      close_modal: function (nClicks) {
        return nClicks ? false : noUpdate();
      },

      close_drawers: function (pathname) {
        return [false, false];
      },

      // Opens or closes a collapse (the shot-details area).
      toggle_open: function (nClicks, opened) {
        return nClicks ? !opened : noUpdate();
      },

      // --- Court: spot selection and view (REQ-007, REQ-009) ---

      // Outputs: court-graph.figure, court-selected-spot.data, court-selected-label.children,
      // court-make.disabled, court-miss.disabled, court-graph-wrap.className. A tap on the court
      // selects the nearest spot; a switch of court-view swaps in the stored figure of that
      // view with the selected spot (if any) still marked, so the selection survives.
      // Drill mode (plan §3.7): a court-queue change auto-advances. When the selected plan
      // spot's attempts (base spots + queued inserts at that key, for the picked player)
      // reach its target, the next plan spot below its target is selected, wrapping; when
      // all are done the label says so. court-base is read as State, not Input: the server
      // sets it just before the ack, so as an Input it would count the acked shot twice
      // (base and queue) for a moment. With court-plan null the queue trigger does nothing.
      select_spot: function (
        clickData, view, queue, figure, figures, selected, base, plan, playerValue,
        confirmed, request
      ) {
        var trigger = triggeredId();
        if (trigger === "court-queue") {
          return advanceSpot(
            figure, selected, base, plan, parsePlayerId(playerValue), queue || [],
            confirmed || [], request
          );
        }
        if (trigger === "court-view") {
          if (!figures || !figures[view]) {
            return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
          }
          var selectedId = hasSpot(selected) ? selected.court_spot_id : null;
          return [
            recolouredFigure(figures[view], selectedId),
            noUpdate(),
            noUpdate(),
            noUpdate(),
            noUpdate(),
            view === VIEW_FULL ? COURT_FULL_CLASS : COURT_CLASS,
          ];
        }
        if (
          !clickData || !clickData.points || !clickData.points.length ||
          !figure || !figure.data || !figure.data.length
        ) {
          return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
        }
        var customdata = clickData.points[0].customdata;
        var spotId = Array.isArray(customdata) ? customdata[0] : customdata;
        var meta = figure.data[0].meta || {};
        var spots = meta.spots || [];
        var chosen = null;
        for (var i = 0; i < spots.length; i++) {
          if (spots[i].court_spot_id === spotId) {
            chosen = spots[i];
            break;
          }
        }
        if (chosen === null) {
          return noUpdates(SELECT_SPOT_OUTPUT_COUNT);
        }
        var selectedSpot = {
          court_spot_id: chosen.court_spot_id,
          spot_number: chosen.spot_number,
          spot_name: chosen.spot_name,
          zone_label: chosen.zone_label,
        };
        var label =
          "#" + chosen.spot_number + " " + chosen.spot_name + " · " + chosen.zone_label;
        return [
          recolouredFigure(figure, spotId),
          selectedSpot,
          label,
          false,
          false,
          noUpdate(),
        ];
      },

      // --- Court: mode switch (REQ-011, REQ-012, REQ-014; plan D2, D4, D5) ---

      // Outputs: className of court-single-panel, court-cumulative-panel, court-manual-panel,
      // court-details-area, court-ft-single-panel, court-ft-cumulative-panel.
      entry_mode_view: function (mode) {
        var single = mode !== MODE_CUMULATIVE && mode !== MODE_MANUAL;
        var shown = function (visible) {
          return visible ? "" : HIDDEN_CLASS;
        };
        return [
          shown(single),
          shown(mode === MODE_CUMULATIVE),
          shown(mode === MODE_MANUAL),
          shown(mode !== MODE_MANUAL),
          shown(single),
          shown(!single),
        ];
      },

      // --- Court: X of Y, No spot and free-throw totals forms (REQ-011, REQ-012, REQ-014) ---

      // Outputs: court-request.data, court-form-feedback.children, then the values of
      // court-cum-makes, court-cum-attempts, court-manual-makes, court-manual-attempts,
      // court-ft-makes, court-ft-attempts. A valid form becomes a request for the dispatcher
      // and only that form's two inputs are cleared.
      entry_form_submit: function (
        cumSave, manualSave, ftSave, spot, cumMakes, cumAttempts, points,
        manMakes, manAttempts, ftMakes, ftAttempts, shotType, hand, creation, defense,
        playerValue
      ) {
        var trigger = triggeredId();
        var clicks = { "court-cum-save": cumSave, "court-manual-save": manualSave,
                       "court-ft-save": ftSave };
        if (!clicks[trigger]) {
          return noUpdates(FORM_OUTPUT_COUNT);
        }
        var result = noUpdates(FORM_OUTPUT_COUNT);
        var playerId = parsePlayerId(playerValue);
        if (isNaN(playerId)) {
          result[1] = MESSAGE_PICK_PLAYER;
          return result;
        }
        var request;
        var makes;
        var attempts;
        var clearFrom;
        if (trigger === "court-cum-save") {
          if (!hasSpot(spot)) {
            result[1] = MESSAGE_TAP_SPOT;
            return result;
          }
          makes = parseCount(cumMakes);
          attempts = parseCount(cumAttempts);
          request = { table: TABLE_SHOT, entry_mode: MODE_CUMULATIVE,
                      court_spot_id: spot.court_spot_id, point_value: null,
                      details: detailsOf(shotType, hand, creation, defense) };
          clearFrom = 2;
        } else if (trigger === "court-manual-save") {
          makes = parseCount(manMakes);
          attempts = parseCount(manAttempts);
          request = { table: TABLE_SHOT, entry_mode: MODE_MANUAL, court_spot_id: null,
                      point_value: points === "3" ? 3 : 2, details: null };
          clearFrom = 4;
        } else {
          makes = parseCount(ftMakes);
          attempts = parseCount(ftAttempts);
          request = { table: TABLE_FREE_THROW, entry_mode: MODE_CUMULATIVE,
                      court_spot_id: null, point_value: null, details: null };
          clearFrom = 6;
        }
        if (!countsValid(makes, attempts)) {
          result[1] = MESSAGE_COUNTS;
          return result;
        }
        request.request_id = newCommandKey();
        request.player_id = playerId;
        request.makes = makes;
        request.attempts = attempts;
        result[0] = request;
        result[1] = "";
        result[clearFrom] = "";
        result[clearFrom + 1] = "";
        return result;
      },

      // --- Court: shot details remembered for the tab session (REQ-013; plan D5) ---

      // Outputs: court-details-memory.data (no_update when unchanged, which stops the
      // remember/restore loop) and court-details-summary.children.
      details_remember: function (shotType, hand, creation, defense, memory, shotTypeOptions) {
        var parts = [];
        if (detailValue(shotType) !== null) {
          parts.push(shotTypeLabel(shotType, shotTypeOptions));
        }
        [hand, creation, defense].forEach(function (value) {
          if (detailValue(value) !== null) {
            parts.push(DETAIL_LABELS[value] || value);
          }
        });
        var summary = parts.length ? parts.join(" · ") : DETAILS_EMPTY_SUMMARY;
        if (memory && sameDetails(memory, shotType, hand, creation, defense)) {
          return [noUpdate(), summary];
        }
        return [
          {
            shot_type_code: detailValue(shotType),
            hand: detailValue(hand),
            creation: detailValue(creation),
            defense: detailValue(defense),
          },
          summary,
        ];
      },

      // Outputs: the values of court-detail-shot-type, -hand, -creation, -defense. Runs when
      // the session store loads (initial call) and after each remember; does nothing when the
      // memory is empty or already equal to the controls.
      details_restore: function (modifiedTimestamp, memory, shotType, hand, creation, defense) {
        if (!memory || typeof memory !== "object") {
          return noUpdates(DETAIL_COUNT);
        }
        if (sameDetails(memory, shotType, hand, creation, defense)) {
          return noUpdates(DETAIL_COUNT);
        }
        return [
          detailValue(memory.shot_type_code),
          detailValue(memory.hand) || DETAIL_NONE,
          detailValue(memory.creation) || DETAIL_NONE,
          detailValue(memory.defense) || DETAIL_NONE,
        ];
      },

      // Outputs: the four detail values, all unset.
      clear_details: function (nClicks) {
        if (!nClicks) {
          return noUpdates(DETAIL_COUNT);
        }
        return [null, DETAIL_NONE, DETAIL_NONE, DETAIL_NONE];
      },

      // --- Court: the single writer of the command stores (plan §3.6) ---

      // Outputs: court-queue.data, court-confirmed.data, court-outbox.data,
      // court-failure.data. An output that did not change returns no_update, so an unchanged
      // outbox never re-fires the server callback. parent (entry-parent) is not sent; the
      // server reads it from the store. court-ack arrives from the server's set_props; a late
      // ack whose key or attempt no longer matches the outbox is ignored. playerValue
      // (court-player.value) is the entry player: Make, Miss, FT Make, FT Miss and Undo
      // stamp it as player_id and do nothing while it is not a valid id; a court-request
      // already carries it. court-failure is {failed_at, auto_retries} while saving fails:
      // the watchdog resends the head every COURT_AUTO_RETRY_INTERVAL_MS, at most
      // COURT_AUTO_RETRY_MAX times; a Retry tap restarts the count (REQ-058, plan D9).
      // Drill mode (entry-parent.kind "drill"): every insert also carries side
      // (court-drill-side) and new_attempt_after (the pending court-attempt-request of its
      // player, else null), and confirmed entries keep their spot_key for the plan progress.
      court_dispatch: function (
        makeClicks, missClicks, ftMakeClicks, ftMissClicks, undoClicks, retryClicks,
        request, ack, watchdogTicks, spot, queue, confirmed, outbox, failure, parent,
        playerValue, shotType, hand, creation, defense, attemptRequest, drillSide, base
      ) {
        var trigger = triggeredId();
        var playerId = parsePlayerId(playerValue);
        var nextQueue = (queue || []).slice();
        var nextConfirmed = (confirmed || []).slice();
        var nextOutbox = outbox || null;
        var nextFailure = failure || null;
        var queueChanged = false;
        var confirmedChanged = false;
        var outboxChanged = false;
        var failureChanged = false;
        // False after a failed or timed-out send: the head waits for a retry.
        var maySend = true;
        var now = Date.now();

        if (trigger === "court-make" || trigger === "court-miss") {
          if (isNaN(playerId) || !hasSpot(spot)) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue.push(withDrillFields(insertCommand(
            playerId, TABLE_SHOT, MODE_SINGLE, spot.court_spot_id, null,
            trigger === "court-make" ? 1 : 0, 1,
            detailsOf(shotType, hand, creation, defense)
          ), parent, attemptRequest, drillSide, base));
          queueChanged = true;
        } else if (trigger === "court-ft-make" || trigger === "court-ft-miss") {
          if (isNaN(playerId)) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue.push(withDrillFields(insertCommand(
            playerId, TABLE_FREE_THROW, MODE_SINGLE, null, null,
            trigger === "court-ft-make" ? 1 : 0, 1, null
          ), parent, attemptRequest, drillSide, base));
          queueChanged = true;
        } else if (trigger === "court-request") {
          if (!request || typeof request !== "object" || !request.request_id) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue.push(withDrillFields(insertCommand(
            request.player_id, request.table, request.entry_mode, request.court_spot_id,
            request.point_value, request.makes, request.attempts, request.details
          ), parent, attemptRequest, drillSide, base));
          queueChanged = true;
        } else if (trigger === "court-undo") {
          if (isNaN(playerId)) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          var undone = applyUndo(nextQueue, nextConfirmed, nextOutbox, playerId);
          if (undone === null) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue = undone;
          queueChanged = true;
        } else if (trigger === "court-retry") {
          if (nextOutbox || !nextQueue.length) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextFailure = newFailure(0);
          failureChanged = true;
        } else if (trigger === "court-ack") {
          if (
            !ack || !nextOutbox || ack.key !== nextOutbox.key ||
            ack.attempt !== nextOutbox.attempt || ack.op !== nextOutbox.op
          ) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          if (ack.ok) {
            var sent = nextOutbox;
            nextQueue = nextQueue.filter(function (command) {
              return !(command.key === sent.key && command.op === sent.op);
            });
            if (sent.op === "insert") {
              // The command's player, not the picker's: the picker may have changed since.
              var confirmedEntry = {
                key: sent.key,
                table: sent.table,
                entry_id: ack.entry_id,
                makes: sent.makes,
                attempts: sent.attempts,
                player_id: sent.player_id,
              };
              if (parent && parent.kind === PARENT_KIND_DRILL) {
                confirmedEntry.spot_key = spotKeyOf(sent);
              }
              nextConfirmed.push(confirmedEntry);
            } else {
              nextConfirmed = nextConfirmed.filter(function (entry) {
                return entry.key !== sent.key;
              });
            }
            queueChanged = true;
            confirmedChanged = true;
            if (nextFailure !== null) {
              nextFailure = null;
              failureChanged = true;
            }
          } else {
            maySend = false;
            nextFailure = newFailure(nextFailure ? nextFailure.auto_retries : 0);
            failureChanged = true;
          }
          nextOutbox = null;
          outboxChanged = true;
        } else if (trigger === "court-watchdog") {
          if (nextOutbox) {
            if (now - nextOutbox.sent_at <= COURT_SEND_TIMEOUT_MS) {
              return noUpdates(COMMAND_STORE_COUNT);
            }
            nextOutbox = null;
            outboxChanged = true;
            maySend = false;
            nextFailure = newFailure(nextFailure ? nextFailure.auto_retries : 0);
            failureChanged = true;
          } else if (
            nextQueue.length && nextFailure &&
            nextFailure.auto_retries < COURT_AUTO_RETRY_MAX &&
            now - nextFailure.failed_at >= COURT_AUTO_RETRY_INTERVAL_MS
          ) {
            nextFailure = newFailure(nextFailure.auto_retries + 1);
            failureChanged = true;
          } else {
            return noUpdates(COMMAND_STORE_COUNT);
          }
        } else {
          return noUpdates(COMMAND_STORE_COUNT);
        }

        if (maySend && !nextOutbox && nextQueue.length) {
          var prepared = prepareSend(nextQueue, nextConfirmed);
          nextQueue = prepared.queue;
          nextOutbox = prepared.outbox;
          queueChanged = true;
          outboxChanged = true;
        }
        if (!nextOutbox && !nextQueue.length && nextFailure !== null) {
          nextFailure = null;
          failureChanged = true;
        }

        return [
          queueChanged ? nextQueue : noUpdate(),
          confirmedChanged ? nextConfirmed : noUpdate(),
          outboxChanged ? nextOutbox : noUpdate(),
          failureChanged ? nextFailure : noUpdate(),
        ];
      },

      // --- Court: display, a pure function of the stores (plan §3.6) ---

      // Outputs: court-tally.children, court-ft-tally.children, court-status.children,
      // court-status.color, court-retry.style, court-undo.disabled, court-watchdog.disabled.
      // The tallies and Undo are for the picked player; the save status covers every
      // player's commands. The watchdog runs while a command is in flight or while a failed
      // save still has automatic retries left. Drill mode: while the picked player has a
      // pending New attempt request (court-attempt-request), the tallies start from 0
      // instead of the base (plan §3.7); on other screens the request store stays {}.
      court_render: function (
        base, queue, confirmed, outbox, ack, failure, playerValue, attemptRequest
      ) {
        var playerId = parsePlayerId(playerValue);
        var pending = queue || [];
        var done = confirmed || [];
        var tallyBase = isPending(base, attemptRequest, playerId)
          ? baseFromZero(base, playerId)
          : base;
        var totals = playerTotals(tallyBase, pending, done, playerId);
        var entries = effectiveEntries(pending, done, playerId);

        var failed = !outbox && pending.length > 0;
        var retriesLeft = !!failure && failure.auto_retries < COURT_AUTO_RETRY_MAX;
        var statusText = STATUS_READY.text;
        var statusColor = STATUS_READY.color;
        if (outbox) {
          statusText = STATUS_SAVING.text;
          statusColor = STATUS_SAVING.color;
        } else if (failed) {
          statusText = "Save failed - " + pending.length + " not saved";
          if (ack && ack.ok === false && ack.ref) {
            statusText += " (" + ack.ref + ")";
          }
          statusText += retriesLeft ? ". Retrying..." : ". Tap Retry.";
          statusColor = STATUS_FAILED_COLOR;
        } else if (ack && ack.ok) {
          statusText = "Saved " + ack.saved_at;
          statusColor = STATUS_SAVED_COLOR;
        }

        return [
          tallyText(totals.shot.makes, totals.shot.attempts),
          tallyText(totals.free_throw.makes, totals.free_throw.attempts),
          statusText,
          statusColor,
          failed ? RETRY_SHOWN_STYLE : RETRY_HIDDEN_STYLE,
          entries.length === 0 || failed,
          !(outbox || (failed && retriesLeft)),
        ];
      },

      // --- Drill mode: attempts, plan progress, personal best (plan §3.7) ---

      // Output: court-attempt-request.data (single writer). "New attempt" or a change of
      // court-drill-side asks for the next shot of the picked player to start the attempt
      // after the latest one: request[player] = court-base[player].attempt_no (0 when none).
      new_attempt_request: function (newClicks, side, request, base, playerValue) {
        var trigger = triggeredId();
        if (trigger === "court-new-attempt" && !newClicks) {
          return noUpdate();
        }
        var playerId = parsePlayerId(playerValue);
        if (isNaN(playerId)) {
          return noUpdate();
        }
        var key = String(playerId);
        var playerBase = (base && base[key]) || {};
        var next = Object.assign({}, request || {});
        next[key] = playerBase.attempt_no || 0;
        return next;
      },

      // Output: court-plan-progress.children. The attempt in progress, the drill total
      // (court-base result totals plus queued inserts) and one line per plan spot with its
      // attempts and target, the selected spot marked. "" without a plan.
      drill_progress: function (plan, base, queue, playerValue, request, selected, confirmed) {
        if (!plan || !plan.spots) {
          return "";
        }
        var playerId = parsePlayerId(playerValue);
        if (isNaN(playerId)) {
          return "";
        }
        var pending = isPending(base, request, playerId);
        var commands = queue || [];
        var done = confirmed || [];
        var playerBase = (base && base[String(playerId)]) || {};
        var attemptNo = playerBase.attempt_no || 0;
        var attemptText = pending
          ? "Attempt " + (attemptNo + 1) + " (new)"
          : "Attempt " + Math.max(attemptNo, 1);
        var resultBase = {};
        resultBase[String(playerId)] = {
          makes: playerBase.result_makes || 0,
          attempts: playerBase.result_attempts || 0,
          ft_makes: 0,
          ft_attempts: 0,
        };
        var total = playerTotals(resultBase, commands, done, playerId);
        var lines = [
          attemptText + " · Drill total " + tallyText(
            total.shot.makes + total.free_throw.makes,
            total.shot.attempts + total.free_throw.attempts
          ),
        ];
        var counts = spotCounts(base, commands, done, playerId, pending);
        var selectedId = hasSpot(selected) ? selected.court_spot_id : null;
        plan.spots.forEach(function (spot) {
          var line = spot.label + " " + (counts[spot.key] || 0);
          if (spot.target !== null && spot.target !== undefined) {
            line += " / " + spot.target;
          }
          if (selectedId !== null && spot.court_spot_id === selectedId) {
            line += CURRENT_SPOT_MARK;
          }
          lines.push(line);
        });
        return lines.join("\n");
      },

      // Output: court-drill-pb.children: the picked player's personal best and earlier
      // attempts, from court-drill-pb-data (read on page open, D16).
      drill_pb_text: function (playerValue, pbData) {
        var playerId = parsePlayerId(playerValue);
        if (isNaN(playerId) || !pbData) {
          return NO_PERSONAL_BEST;
        }
        return pbData[String(playerId)] || NO_PERSONAL_BEST;
      },

      // --- Run mode: stopwatch and countdown (REQ-056, D17) ---

      // Outputs: drill-timer.data, drill-timer-display.children, drill-timer-tick.disabled,
      // drill-value-1.value. Single writer of all four (drill-value-1 only on stopwatch Stop,
      // which writes the elapsed seconds with 2 decimals). Time is measured with Date.now().
      // At zero the countdown shows "Time!" and vibrates when the device can.
      timer_step: function (startClicks, stopClicks, resetClicks, ticks, timer) {
        if (!timer || typeof timer !== "object") {
          return noUpdates(TIMER_OUTPUT_COUNT);
        }
        var trigger = triggeredId();
        var clicks = { "drill-timer-start": startClicks, "drill-timer-stop": stopClicks,
                       "drill-timer-reset": resetClicks };
        if (trigger in clicks && !clicks[trigger]) {
          return noUpdates(TIMER_OUTPUT_COUNT);
        }
        var now = Date.now();
        var next = Object.assign({}, timer);
        var limitMs = (timer.limit_s || 0) * MS_PER_SECOND;
        var isCountdown = timer.mode === TIMER_MODE_COUNTDOWN;
        if (trigger === "drill-timer-start") {
          if (timer.running || (isCountdown && timerElapsed(timer, now) >= limitMs)) {
            return noUpdates(TIMER_OUTPUT_COUNT);
          }
          next.running = true;
          next.started_at = now;
          return [next, timerDisplay(next, now), false, noUpdate()];
        }
        if (trigger === "drill-timer-stop") {
          if (!timer.running) {
            return noUpdates(TIMER_OUTPUT_COUNT);
          }
          next.elapsed_ms = timerElapsed(timer, now);
          next.running = false;
          next.started_at = null;
          var value = timer.mode === TIMER_MODE_STOPWATCH
            ? Math.round(next.elapsed_ms / 10) / 100
            : noUpdate();
          return [next, timerDisplay(next, now), true, value];
        }
        if (trigger === "drill-timer-reset") {
          next.running = false;
          next.started_at = null;
          next.elapsed_ms = 0;
          return [next, timerDisplay(next, now), true, noUpdate()];
        }
        if (!timer.running) {
          return [noUpdate(), noUpdate(), true, noUpdate()];
        }
        if (isCountdown && timerElapsed(timer, now) >= limitMs) {
          next.running = false;
          next.started_at = null;
          next.elapsed_ms = limitMs;
          if (navigator && typeof navigator.vibrate === "function") {
            navigator.vibrate(TIMER_VIBRATE_MS);
          }
          return [next, TIMER_DONE_TEXT, true, noUpdate()];
        }
        return [noUpdate(), timerDisplay(timer, now), noUpdate(), noUpdate()];
      },

      // --- Run mode: outcome buttons (REQ-077) ---

      // Outputs: outcome-pending.data, outcome-pending-text.children, outcome-submit.data.
      // Records the tapped button for its group; once every group has a pick it writes the
      // rep {key, picks: {group_id: outcome_id}, player_id, side, tapped_at} to
      // outcome-submit and clears the pending picks. With one group every tap is a rep.
      // Picks of another player are dropped when the picker changes.
      outcome_pick: function (nClicksList, pending, groups, playerValue, side) {
        var pattern = triggeredPattern();
        if (!pattern || !triggeredValue()) {
          return noUpdates(OUTCOME_OUTPUT_COUNT);
        }
        var playerId = parsePlayerId(playerValue);
        var groupList = groups || [];
        if (isNaN(playerId) || !groupList.length) {
          return noUpdates(OUTCOME_OUTPUT_COUNT);
        }
        var picks = pending && pending.player_id === playerId
          ? Object.assign({}, pending.picks || {})
          : {};
        picks[String(pattern.group)] = pattern.index;
        var complete = groupList.every(function (group) {
          return picks[String(group.id)] !== undefined;
        });
        if (complete) {
          return [
            null,
            "",
            {
              key: newCommandKey(),
              picks: picks,
              player_id: playerId,
              side: side === "left" || side === "right" ? side : null,
              tapped_at: new Date().toISOString(),
            },
          ];
        }
        var parts = groupList.map(function (group) {
          var picked = picks[String(group.id)];
          var labels = group.labels || {};
          return group.name + ": " + (picked !== undefined ? labels[String(picked)] : "...");
        });
        return [{ player_id: playerId, picks: picks }, parts.join(" · "), noUpdate()];
      },
    };
  })(),
});
