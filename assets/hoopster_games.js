/*
 * Hoopster clientside callback functions of games, namespace "hoopster_games" (task
 * 2026-10-23_games, plan §3.3, §3.4, items 2.4, 2.6 and 3.6). Registered by
 * src/controller_games.py and src/controller_home.py with
 * ClientsideFunction("hoopster_games", "<name>").
 *
 * Kept apart from assets/hoopster.js (namespace "hoopster", the court code) so the two never
 * edit one file. Every function is pure apart from Date.now() and makes no network call.
 *
 * The live +1 stats use one serialised channel, like the court (plan §3.3): exactly one
 * function, game_stat_dispatch, writes game-stat-queue, game-stat-confirmed and
 * game-stat-outbox. game-stat-ack and game-stat-base are written by the server through
 * set_props, which starts a fresh Dash callback chain; give no callback an Output on them.
 *
 * Commands: {op: "add" | "undo", key, attempt, sent_at, stat, expected}; an undo also carries
 * target_key, the key of the confirmed add it cancels. expected is game-stat-base[stat] when
 * the command is sent (only one command is ever in flight). The queue holds every command not
 * yet confirmed, the one in flight included (as its head). A failed or lost send waits for
 * Retry: while the queue is not empty and nothing is in flight, new taps are queued only.
 */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  hoopster_games: (function () {
    "use strict";

    // Duplicated from config/app_config.py GAME_STAT_SEND_TIMEOUT_MS.
    var SEND_TIMEOUT_MS = 10000;
    // Duplicated from src/layouts_games.py PRACTICE_GAME_CODE, HIDDEN_STYLE and SHOWN_STYLE.
    var PRACTICE_GAME_CODE = "practice_game";
    var HIDDEN_STYLE = { display: "none" };
    var SHOWN_STYLE = {};

    var OP_ADD = "add";
    var OP_UNDO = "undo";
    var ADD_BUTTON_TYPE = "game-stat-add";
    var COMMAND_STORE_COUNT = 3;

    var KEY_SUFFIX_LENGTH = 6;
    var KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

    // Mantine colour names for the save-status badge (REQ-058), as in court_render.
    var STATUS_READY = { text: "Ready", color: "gray" };
    var STATUS_SAVING = { text: "Saving...", color: "blue" };
    var STATUS_FAILED_COLOR = "red";
    var STATUS_SAVED_COLOR = "green";
    var RETRY_SHOWN_STYLE = {};
    var RETRY_HIDDEN_STYLE = { display: "none" };

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

    function context() {
      return window.dash_clientside.callback_context || {};
    }

    // A command key unique within the visit: tap time plus a random suffix (not a secret).
    function newCommandKey() {
      var suffix = "";
      for (var i = 0; i < KEY_SUFFIX_LENGTH; i++) {
        suffix += KEY_ALPHABET.charAt(Math.floor(Math.random() * KEY_ALPHABET.length));
      }
      return Date.now() + "-" + suffix;
    }

    function counterOf(base, stat) {
      var value = base ? base[stat] : 0;
      return typeof value === "number" && Number.isInteger(value) ? value : 0;
    }

    // The adds of this visit that still count, oldest first: confirmed adds not targeted by
    // a queued undo, then queued adds. Each item is {key, stat, queued}.
    function effectiveAdds(queue, confirmed) {
      var undone = {};
      var adds = [];
      var i;
      for (i = 0; i < queue.length; i++) {
        if (queue[i].op === OP_UNDO) {
          undone[queue[i].target_key] = true;
        }
      }
      for (i = 0; i < confirmed.length; i++) {
        if (!undone[confirmed[i].key]) {
          adds.push({ key: confirmed[i].key, stat: confirmed[i].stat, queued: false });
        }
      }
      for (i = 0; i < queue.length; i++) {
        if (queue[i].op === OP_ADD) {
          adds.push({ key: queue[i].key, stat: queue[i].stat, queued: true });
        }
      }
      return adds;
    }

    // Undo (REQ-010, multi-step since the screen opened): cancels the newest queued add not
    // yet sent; otherwise queues an undo of the newest add that still counts (FIFO order runs
    // it after that add is confirmed). Returns null when there is nothing to undo.
    function applyUndo(queue, confirmed, outbox) {
      var adds = effectiveAdds(queue, confirmed);
      if (!adds.length) {
        return null;
      }
      var last = adds[adds.length - 1];
      var inFlight = outbox && outbox.key === last.key;
      if (last.queued && !inFlight) {
        return queue.filter(function (command) {
          return !(command.op === OP_ADD && command.key === last.key);
        });
      }
      return queue.concat([
        { op: OP_UNDO, key: newCommandKey(), stat: last.stat, target_key: last.key },
      ]);
    }

    function removeConfirmed(confirmed, key) {
      return confirmed.filter(function (entry) {
        return entry.key !== key;
      });
    }

    // Prepares the queue head for sending with expected = base[stat]. The attempt number
    // counts up per command, so a late ack of an earlier attempt can never match. An undo
    // whose counter is already 0 (changed elsewhere) is dropped with its target, since the
    // server would refuse it on every retry.
    function prepareSend(queue, confirmed, base) {
      var remaining = queue.slice();
      var nextConfirmed = confirmed;
      while (remaining.length) {
        var head = remaining[0];
        var expected = counterOf(base, head.stat);
        if (head.op === OP_UNDO && expected <= 0) {
          remaining.shift();
          nextConfirmed = removeConfirmed(nextConfirmed, head.target_key);
          continue;
        }
        var attempt = (head.attempt || 0) + 1;
        remaining[0] = Object.assign({}, head, { attempt: attempt });
        var outbox = Object.assign({}, head, {
          attempt: attempt,
          sent_at: Date.now(),
          expected: expected,
        });
        return { queue: remaining, confirmed: nextConfirmed, outbox: outbox };
      }
      return { queue: remaining, confirmed: nextConfirmed, outbox: null };
    }

    function isAckFor(ack, outbox) {
      return (
        !!ack && !!outbox && ack.key === outbox.key && ack.attempt === outbox.attempt &&
        ack.op === outbox.op
      );
    }

    return {
      // --- Live +1 stats: the single writer of the command stores (plan §3.3) ---

      // Inputs: {"type": "game-stat-add", "stat": ALL}.n_clicks, game-stat-undo,
      // game-stat-retry, game-stat-ack, game-stat-watchdog. States: game-stat-queue,
      // game-stat-confirmed, game-stat-outbox, game-stat-base. Outputs: game-stat-queue,
      // game-stat-confirmed, game-stat-outbox (no_update when unchanged, so an unchanged
      // outbox never re-fires the server). An ack is accepted only when key, attempt and op
      // match the outbox; its values (when present) are the new base for the next send.
      game_stat_dispatch: function (
        addClicks, undoClicks, retryClicks, ack, watchdogTicks, queue, confirmed, outbox, base
      ) {
        var callbackContext = context();
        var trigger = callbackContext.triggered_id;
        var triggered = callbackContext.triggered || [];
        var triggeredValue = triggered.length ? triggered[0].value : undefined;
        var nextQueue = (queue || []).slice();
        var nextConfirmed = (confirmed || []).slice();
        var nextOutbox = outbox || null;
        var nextBase = base || {};
        var wasFailed = !nextOutbox && nextQueue.length > 0;
        var maySend = !wasFailed;
        var queueChanged = false;
        var confirmedChanged = false;
        var outboxChanged = false;

        if (trigger && typeof trigger === "object" && trigger.type === ADD_BUTTON_TYPE) {
          // Page insertion and re-renders report no real click.
          if (!triggeredValue || typeof trigger.stat !== "string") {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue.push({ op: OP_ADD, key: newCommandKey(), stat: trigger.stat });
          queueChanged = true;
        } else if (trigger === "game-stat-undo") {
          if (!undoClicks || wasFailed) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          var undone = applyUndo(nextQueue, nextConfirmed, nextOutbox);
          if (undone === null) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          nextQueue = undone;
          queueChanged = true;
        } else if (trigger === "game-stat-retry") {
          if (!retryClicks || nextOutbox || !nextQueue.length) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          maySend = true;
        } else if (trigger === "game-stat-ack") {
          if (!isAckFor(ack, nextOutbox)) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          if (ack.values && typeof ack.values === "object") {
            nextBase = ack.values;
          }
          if (ack.ok) {
            var sent = nextOutbox;
            nextQueue = nextQueue.filter(function (command) {
              return !(command.key === sent.key && command.op === sent.op);
            });
            if (sent.op === OP_ADD) {
              nextConfirmed.push({ key: sent.key, stat: sent.stat });
            } else {
              nextConfirmed = removeConfirmed(nextConfirmed, sent.target_key);
            }
            queueChanged = true;
            confirmedChanged = true;
            maySend = true;
          } else {
            maySend = false;
          }
          nextOutbox = null;
          outboxChanged = true;
        } else if (trigger === "game-stat-watchdog") {
          if (!nextOutbox || Date.now() - nextOutbox.sent_at <= SEND_TIMEOUT_MS) {
            return noUpdates(COMMAND_STORE_COUNT);
          }
          // Lost: no answer within the timeout. The head stays queued for Retry.
          nextOutbox = null;
          outboxChanged = true;
          maySend = false;
        } else {
          return noUpdates(COMMAND_STORE_COUNT);
        }

        if (maySend && !nextOutbox && nextQueue.length) {
          var prepared = prepareSend(nextQueue, nextConfirmed, nextBase);
          if (prepared.confirmed !== nextConfirmed) {
            confirmedChanged = true;
          }
          nextQueue = prepared.queue;
          nextConfirmed = prepared.confirmed;
          nextOutbox = prepared.outbox;
          queueChanged = true;
          outboxChanged = true;
        }

        return [
          queueChanged ? nextQueue : noUpdate(),
          confirmedChanged ? nextConfirmed : noUpdate(),
          outboxChanged ? nextOutbox : noUpdate(),
        ];
      },

      // --- Live +1 stats: display, a pure function of the stores (plan §3.3) ---

      // Inputs: game-stat-base, -queue, -confirmed, -outbox, -ack; State game-stat-labels
      // ([{stat, abbr}] in button order). Outputs: the labels of every +1 button (ALL, in
      // layout order = the labels' order), game-stat-status children and color,
      // game-stat-retry style, game-stat-undo disabled, game-stat-watchdog disabled.
      // Label = base + queued adds - queued undos, never below 0: "+1 {ABBR} ({n})".
      game_stat_render: function (base, queue, confirmed, outbox, ack, labels) {
        var pending = queue || [];
        var done = confirmed || [];
        var list = labels || [];
        var shown = {};
        var i;
        for (i = 0; i < list.length; i++) {
          shown[list[i].stat] = counterOf(base, list[i].stat);
        }
        for (i = 0; i < pending.length; i++) {
          if (shown[pending[i].stat] === undefined) {
            continue;
          }
          shown[pending[i].stat] += pending[i].op === OP_ADD ? 1 : -1;
        }
        var buttonLabels = list.map(function (item) {
          return "+1 " + item.abbr + " (" + Math.max(0, shown[item.stat]) + ")";
        });

        var failed = !outbox && pending.length > 0;
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
          statusText += ". Tap Retry.";
          statusColor = STATUS_FAILED_COLOR;
        } else if (ack && ack.ok) {
          statusText = "Saved " + ack.saved_at;
          statusColor = STATUS_SAVED_COLOR;
        }

        return [
          buttonLabels,
          statusText,
          statusColor,
          failed ? RETRY_SHOWN_STYLE : RETRY_HIDDEN_STYLE,
          effectiveAdds(pending, done).length === 0 || failed,
          !outbox,
        ];
      },

      // --- Game form: practice-game-only fields (D8) ---

      // Input: game-new-category.value or game-edit-category.value. Outputs: the style of the
      // "-teammates-wrap" and "-target-wrap" wrappers. Mirrors
      // layouts_games.practice_game_style.
      practice_game_fields: function (category) {
        var style = category === PRACTICE_GAME_CODE ? SHOWN_STYLE : HIDDEN_STYLE;
        return [style, style];
      },

      // --- Home: resume where you left off (REQ-057; plan §3.4, D10) ---

      // Input: home-resume-target.data (a path or null); State: resume-checked.data (session
      // storage). Outputs: url.href, resume-checked.data. On the first Home render of a
      // browser session the flag is set and, with a target, the app navigates there; after
      // that Home only shows the card. href, not pathname: this callback runs inside the
      // url.pathname -> device_clock -> router chain that built Home, so Dash drops a
      // device_clock re-run caused by its own pathname output as circular. Writing href makes
      // dcc.Location push the path and report the new pathname itself, as a dcc.Link does.
      auto_resume: function (target, checked) {
        if (checked) {
          return [noUpdate(), noUpdate()];
        }
        if (typeof target === "string" && target.charAt(0) === "/") {
          return [target, true];
        }
        return [noUpdate(), true];
      },
    };
  })(),
});
