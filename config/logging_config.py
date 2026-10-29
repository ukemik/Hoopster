"""Logging setup for Hoopster processes (COD-4-01, REQ-068).

One stdout handler on the root logger. The format records the logger name and the function
name, so log messages never repeat the function name (COD-4-02). Called once per process: by
main.py for the web application and by the setup script for its own run.
"""

import logging
import sys

logger = logging.getLogger(__name__)


# --- Logging configuration ---

# Logger for request and SQL durations, written only when LOG_TIMINGS is true (REQ-068).
TIMING_LOGGER_NAME = "hoopster.timing"

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(funcName)s: %(message)s"

# Name given to the handler this module installs, so a second call can recognise it.
STDOUT_HANDLER_NAME = "hoopster-stdout"


def configure_logging(level_name: str) -> None:
    """
    Description:
        Installs one StreamHandler on sys.stdout on the root logger with the project format,
        and sets the root level. Equivalent to logging.basicConfig, but idempotent.

    Parameters:
        level_name: DEBUG, INFO, WARNING or ERROR (validated by app_config.load_settings).

    Return Values:
        None.

    Notes:
        A second call only updates the level; it never adds a second handler, so lines are
        not duplicated.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level_name)
    for handler in root_logger.handlers:
        if handler.get_name() == STDOUT_HANDLER_NAME:
            return
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.set_name(STDOUT_HANDLER_NAME)
    stdout_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(stdout_handler)
