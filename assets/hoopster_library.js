/*
 * Hoopster clientside callback functions of the drill library, namespace "hoopster_library"
 * (practice_drills plan §3.4, item 7). Registered by src/controller_drills.py with
 * ClientsideFunction("hoopster_library", "<name>").
 *
 * Kept apart from assets/hoopster.js (namespace "hoopster") so the library and the court
 * code never edit one file. Every function is pure and makes no network call.
 */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  hoopster_library: (function () {
    "use strict";

    // Mirrors src/layouts_drills.py HIDDEN_STYLE / SHOWN_STYLE.
    var HIDDEN = { display: "none" };
    var SHOWN = {};
    // Mirrors src/layouts_drills.py RESULT_TYPE_PHYSICAL, the capture kinds and the labels.
    var PHYSICAL = "physical";
    var CAPTURE_SHOTS = "shots";
    var CAPTURE_OUTCOMES = "outcomes";
    var TARGET_LABEL_SHOTS = "Shots per attempt";
    var TARGET_LABEL_REPS = "Reps per attempt";
    var TARGET_LABEL_DEFAULT = "Target";
    var UNIT_WORDS = { s: "seconds", count: "count" };
    var SECTION_COUNT = 5;

    function style(isShown) {
      return isShown ? SHOWN : HIDDEN;
    }

    return {
      // Inputs: drill-result-type.value; State: drill-result-meta.data
      // ({code: {capture_kind, value_unit, needs_fixed_seconds}}).
      // Outputs: the styles of drill-section-target, -fixed, -physical, -shooting and
      // -outcomes, then drill-target.label. Mirrors layouts_drills.drill_section_styles and
      // drill_target_label; keep the three in step.
      drill_form_sections: function (resultType, meta) {
        var facts = meta && resultType ? meta[resultType] : null;
        if (!facts) {
          var hidden = [];
          for (var i = 0; i < SECTION_COUNT; i++) {
            hidden.push(HIDDEN);
          }
          hidden.push(TARGET_LABEL_DEFAULT);
          return hidden;
        }
        var label = TARGET_LABEL_DEFAULT;
        if (facts.capture_kind === CAPTURE_SHOTS) {
          label = TARGET_LABEL_SHOTS;
        } else if (facts.capture_kind === CAPTURE_OUTCOMES) {
          label = TARGET_LABEL_REPS;
        } else if (facts.value_unit) {
          label = "Target (" + (UNIT_WORDS[facts.value_unit] || facts.value_unit) + ")";
        }
        return [
          style(resultType !== PHYSICAL),
          style(Boolean(facts.needs_fixed_seconds)),
          style(resultType === PHYSICAL),
          style(facts.capture_kind === CAPTURE_SHOTS),
          style(facts.capture_kind === CAPTURE_OUTCOMES),
          label,
        ];
      },
    };
  })(),
});
