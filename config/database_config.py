"""Database connection-pool settings for the SQLAlchemy engine (REQ-048).

The connection URL itself is the secret DATABASE_URL (config/app_config.py); only the pool
shape lives here. The pool is small on purpose: the application runs as one worker process
with a few threads, for two users.
"""

import logging

logger = logging.getLogger(__name__)


# --- Engine pool settings ---

POOL_SIZE = 2
MAX_OVERFLOW = 3
# Recycle connections periodically so one closed by the server or network while idle is not reused.
POOL_RECYCLE_SECONDS = 300
# Test a pooled connection before use, so a connection dropped while idle is replaced.
POOL_PRE_PING = True
# Keep bound parameters (password hashes, key hashes, emails) out of SQLAlchemy exception
# text, which logger.exception writes to the log (COD-4-05).
HIDE_PARAMETERS = True


def engine_options() -> dict[str, int | bool]:
    """
    Description:
        Returns the engine settings above under the keyword names create_engine expects.

    Parameters:
        None.

    Return Values:
        dict[str, int | bool]: pool_size, max_overflow, pool_recycle, pool_pre_ping and
        hide_parameters.

    Notes:
        Used as create_engine(url, **engine_options()) by data_handler.init_engine.
    """
    return {
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_recycle": POOL_RECYCLE_SECONDS,
        "pool_pre_ping": POOL_PRE_PING,
        "hide_parameters": HIDE_PARAMETERS,
    }
