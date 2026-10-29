"""Data access for the home page: the recent-activity list (REQ-041) and the resume target
(REQ-057).

Read-only. Runs its SQL through data_handler.
"""

import logging
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Recent activity (REQ-041) ---

# has_drills (practice_drills 2b, plan §3.5 "Coexistence"): a practice has drills when it was
# started from a template, or has a live placed set or a live practice drill. Always false for
# games and measurements.
LIST_RECENT_ACTIVITY_SQL = """
SELECT ra.entity, ra.entity_id, ra.event_date, ra.label,
       (ra.entity = 'practice_session'
        AND (EXISTS (SELECT 1 FROM h_data.practice_sessions ps
                     WHERE ps.practice_session_id = ra.entity_id
                       AND ps.practice_template_version_id IS NOT NULL)
             OR EXISTS (SELECT 1 FROM h_data.practice_drill_sets pds
                        WHERE pds.practice_session_id = ra.entity_id
                          AND pds.deleted_at IS NULL)
             OR EXISTS (SELECT 1 FROM h_data.practice_drills pd
                        WHERE pd.practice_session_id = ra.entity_id
                          AND pd.deleted_at IS NULL))) AS has_drills
FROM h_analytics.v_recent_activity ra
WHERE ra.player_id = :player_id
ORDER BY ra.updated_at DESC, ra.entity_id DESC
LIMIT :limit
"""


def list_recent_activity(session: Session, player_id: int, limit: int) -> list[dict[str, object]]:
    """
    Description:
        Lists the player's most recently changed games, practices and measurements.

    Parameters:
        session: the request session.
        player_id: h_data.players.player_id from app_controller.current_player_id(). Never a
            user id (COD-8-01).
        limit: how many items to return (RECENT_ACTIVITY_LIMIT).

    Return Values:
        list[dict[str, object]]: {"entity", "entity_id", "event_date", "label",
        "has_drills"} per item, newest first. entity is 'game', 'practice_session' or
        'physical_measurement'; event_date is a calendar date, shown as stored (REQ-055);
        has_drills is True for a practice with a template, a live placed set or a live
        practice drill (practice_drills D3), else False.

    Notes:
        The has_drills test is three EXISTS subqueries in the same statement.
    """
    return fetch_all(
        session, LIST_RECENT_ACTIVITY_SQL, {"player_id": player_id, "limit": limit}
    )


# --- Resume where you left off (REQ-057; task 2026-10-23_games, plan §3.4, D10) ---

RESUME_KIND_GAME = "game"
RESUME_KIND_PRACTICE = "practice"

# The newest live in-progress game of any live profile dated the device's today.
RESUME_GAME_SQL = """
SELECT g.game_id, btrim(g.opponent_name) AS opponent_name
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND g.entry_status = 'in_progress'
  AND g.game_date = CAST(:today AS date)
ORDER BY g.game_id DESC
LIMIT 1
"""

# The newest live practice dated today with a live participant whose profile is live. has_drills
# is the recent list's test (practice_drills D3), so resume opens the same screen (games
# review_1 Minor 1).
RESUME_PRACTICE_SQL = """
SELECT ps.practice_session_id, pt.label AS practice_type_label,
       (ps.practice_template_version_id IS NOT NULL
        OR EXISTS (SELECT 1 FROM h_data.practice_drill_sets pds
                   WHERE pds.practice_session_id = ps.practice_session_id
                     AND pds.deleted_at IS NULL)
        OR EXISTS (SELECT 1 FROM h_data.practice_drills pd
                   WHERE pd.practice_session_id = ps.practice_session_id
                     AND pd.deleted_at IS NULL)) AS has_drills
FROM h_data.practice_sessions ps
JOIN h_ref.practice_types pt ON pt.practice_type_code = ps.practice_type_code
WHERE ps.deleted_at IS NULL
  AND ps.session_date = CAST(:today AS date)
  AND EXISTS (SELECT 1
              FROM h_data.practice_players pp
              JOIN h_data.players p ON p.player_id = pp.player_id
              WHERE pp.practice_session_id = ps.practice_session_id
                AND pp.deleted_at IS NULL
                AND p.deleted_at IS NULL)
ORDER BY ps.practice_session_id DESC
LIMIT 1
"""


def get_resume_target(session: Session, today: date) -> dict[str, object] | None:
    """
    Description:
        Finds what to resume when the app is reopened (REQ-057): the newest live in-progress
        game of any live profile dated today; else the newest live practice dated today that
        has a live participant with a live profile.

    Parameters:
        session: the request session.
        today: the device's date (device-clock.today), never the server's (REQ-055).

    Return Values:
        dict[str, object] | None: {"kind": "game" | "practice", "id": int, "label": str,
        "has_drills": bool}, e.g. "Live game vs Eagles" (or "Live game" without an opponent)
        or "Practice (Team practice)"; None when there is nothing to resume. has_drills is
        False for a game; for a practice it is True when it was started from a template or
        has a live placed set or a live practice drill, the same test as
        list_recent_activity (practice_drills D3).

    Notes:
        Read only, at most two statements. Practices have no "finish" yet, so the practice
        rule is by date only (plan §8 gap 1). The practice row is the practice itself, so
        its template is read directly; the two other tests are EXISTS subqueries in the same
        statement.
    """
    game = fetch_one(session, RESUME_GAME_SQL, {"today": today})
    if game is not None:
        opponent = game.get("opponent_name")
        label = f"Live game vs {opponent}" if opponent else "Live game"
        return {
            "kind": RESUME_KIND_GAME,
            "id": int(cast(int, game["game_id"])),
            "label": label,
            "has_drills": False,
        }
    practice = fetch_one(session, RESUME_PRACTICE_SQL, {"today": today})
    if practice is not None:
        return {
            "kind": RESUME_KIND_PRACTICE,
            "id": int(cast(int, practice["practice_session_id"])),
            "label": f"Practice ({practice['practice_type_label']})",
            "has_drills": bool(practice["has_drills"]),
        }
    return None
