"""Database engine, request-scoped session and query helpers (REQ-048, REQ-068).

Every query in the application runs through fetch_all, fetch_one, fetch_table or execute here, so each
one is a SQLAlchemy text() statement with bound parameters, and a failure is logged and
rolled back in one place (COD-6-02). This module never commits: the only commit is in
app_controller.run_write (COD-6-01).
"""

import logging
import time
from collections.abc import Mapping
from typing import cast

from psycopg import Error as PsycopgError
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, CursorResult, Engine
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from config.database_config import engine_options
from config.logging_config import TIMING_LOGGER_NAME

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger(TIMING_LOGGER_NAME)

# Set once by init_engine; read by every request thread.
_engine: Engine | None = None
_session_registry: scoped_session[Session] | None = None

# Key in Connection.info holding the start times of the statements running on it.
QUERY_START_KEY = "hoopster_query_start"
# Statement text is shortened to this many characters in the timing log.
TIMING_STATEMENT_CHARS = 120
MILLISECONDS_PER_SECOND = 1000.0

SET_ACTING_USER_SQL = "SELECT h_system.fn_set_acting_user(CAST(:user_id AS bigint))"

# Exceptions whose text can carry PostgreSQL's DETAIL line (a whole row, password hashes
# included), so they are logged by class and SQLSTATE only (COD-4-04, COD-4-05). DBAPIError
# is a subclass of StatementError; it is listed for clarity.
DATABASE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    DBAPIError,
    StatementError,
    PsycopgError,
)


# --- Engine and session lifecycle ---

def init_engine(database_url: str, log_timings: bool) -> None:
    """
    Description:
        Creates the process's engine and its thread-scoped session registry, and attaches
        the SQL timing listeners when timing is on.

    Parameters:
        database_url: the SQLAlchemy URL from DATABASE_URL (role hoopster_app).
        log_timings: True attaches the cursor timing listeners (REQ-068).

    Return Values:
        None.

    Notes:
        Lazy: no connection is opened until the first query. Call once per process; a
        second call raises RuntimeError.
    """
    global _engine, _session_registry
    if _session_registry is not None:
        logger.error("Database engine is already initialised")
        raise RuntimeError("Database engine is already initialised")
    engine = create_engine(database_url, **engine_options())
    if log_timings:
        event.listen(engine, "before_cursor_execute", _start_query_timer)
        event.listen(engine, "after_cursor_execute", _log_query_duration)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _engine = engine
    _session_registry = scoped_session(session_factory)


def get_session() -> Session:
    """
    Description:
        Returns the session of the current thread (one per request), creating it on first
        use.

    Parameters:
        None.

    Return Values:
        Session: the thread's SQLAlchemy session.

    Notes:
        Raises RuntimeError if init_engine has not been called.
    """
    if _session_registry is None:
        logger.error("Database engine is not initialised; call init_engine first")
        raise RuntimeError("Database engine is not initialised")
    return _session_registry()


def remove_session(exception: BaseException | None = None) -> None:
    """
    Description:
        Closes and discards the current thread's session, returning its connection to the
        pool. Registered as Flask's teardown_appcontext handler.

    Parameters:
        exception: the exception that ended the request, if any (passed by Flask; unused,
            because any open transaction is rolled back by the close).

    Return Values:
        None.

    Notes:
        Does nothing before init_engine, so a request that never needed the database is
        unaffected.
    """
    if _session_registry is None:
        return
    _session_registry.remove()


# --- Audit context ---

def set_acting_user(session: Session, user_id: int | None) -> None:
    """
    Description:
        Sets the acting user for the current transaction through
        h_system.fn_set_acting_user, so the audit triggers can stamp every row written
        after it (COD-7-01).

    Parameters:
        session: the request session, inside the write transaction.
        user_id: h_system.users.user_id of the acting user: current_user.user_id, or
            SYSTEM_USER_ID (0) for writes before sign-in and by the setup script. Never a
            player or device id.

    Return Values:
        None.

    Notes:
        Raises ValueError, before touching the database, when user_id is None, not an int
        (bool is refused) or negative. Must be the first statement of every write
        transaction; app_controller.run_write calls it.
    """
    if user_id is None or isinstance(user_id, bool) or not isinstance(user_id, int):
        raise ValueError("Acting user id must be an integer (COD-7-01)")
    if user_id < 0:
        raise ValueError("Acting user id must not be negative (COD-7-01)")
    execute(session, SET_ACTING_USER_SQL, {"user_id": user_id})


# --- Database error summaries (COD-4-04, COD-4-05) ---

def find_database_error(exc: BaseException) -> BaseException | None:
    """
    Description:
        Walks an exception and its cause/context chain and returns the first database
        driver or statement error in it.

    Parameters:
        exc: the exception being handled.

    Return Values:
        BaseException | None: the database error, or None when the chain holds none.

    Notes:
        A PostgreSQL error's text includes the server's DETAIL line, which for a CHECK
        failure repeats the whole row (password hashes included), so such errors are logged
        without their text (COD-4-05). Lives in the data layer so that both this module and
        the controllers can use it without the data layer importing a controller.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, DATABASE_ERROR_TYPES):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def database_error_summary(exc: BaseException) -> tuple[str, str | None] | None:
    """
    Description:
        The loggable facts of a database error: the class name of the driver's exception
        and its SQLSTATE. Never the error's text.

    Parameters:
        exc: the exception being handled.

    Return Values:
        tuple[str, str | None] | None: (class name, SQLSTATE or None when the error carries
        none), or None when the exception chain holds no database error.

    Notes:
        For a SQLAlchemy wrapper the driver's original exception (.orig) is described.
    """
    database_error = find_database_error(exc)
    if database_error is None:
        return None
    original = getattr(database_error, "orig", None) or database_error
    sqlstate = getattr(original, "sqlstate", None)
    return type(original).__name__, (str(sqlstate) if sqlstate is not None else None)


def _log_sql_failure(exc: BaseException) -> None:
    """
    Description:
        Logs a failed statement: a database error by class and SQLSTATE only, anything else
        with its traceback.

    Parameters:
        exc: the exception caught by fetch_all, fetch_one or execute.

    Return Values:
        None.

    Notes:
        Call only inside the except block, which then rolls back and re-raises (COD-4-04).
        stacklevel=2 makes the log line name the query helper, not this function (COD-4-02).
    """
    summary = database_error_summary(exc)
    if summary is None:
        logger.exception("SQL failed", stacklevel=2)
        return
    error_class, sqlstate = summary
    logger.error(
        "SQL failed [database error %s, SQLSTATE %s]", error_class, sqlstate, stacklevel=2
    )


# --- Query helpers ---

def fetch_all(
    session: Session, sql: str, params: Mapping[str, object] | None = None
) -> list[dict[str, object]]:
    """
    Description:
        Runs one statement and returns every row as a dict keyed by column name.

    Parameters:
        session: the request session.
        sql: schema-qualified SQL with :name placeholders; never built from external values.
        params: the bound parameter values, or None.

    Return Values:
        list[dict[str, object]]: the rows, possibly empty.

    Notes:
        On failure: logs (a database error by class and SQLSTATE only), rolls back the
        session and re-raises (COD-4-04, COD-6-02).
    """
    try:
        result = session.execute(text(sql), dict(params) if params is not None else {})
        return [dict(row) for row in result.mappings()]
    except Exception as exc:
        _log_sql_failure(exc)
        session.rollback()
        raise


def fetch_one(
    session: Session, sql: str, params: Mapping[str, object] | None = None
) -> dict[str, object] | None:
    """
    Description:
        Runs one statement and returns its first row as a dict, or None when there is none.

    Parameters:
        session: the request session.
        sql: schema-qualified SQL with :name placeholders; never built from external values.
        params: the bound parameter values, or None.

    Return Values:
        dict[str, object] | None: the first row, or None.

    Notes:
        Also used for INSERT ... RETURNING. On failure: logs (a database error by class and
        SQLSTATE only), rolls back the session and re-raises (COD-4-04, COD-6-02).
    """
    try:
        result = session.execute(text(sql), dict(params) if params is not None else {})
        row = result.mappings().first()
        return dict(row) if row is not None else None
    except Exception as exc:
        _log_sql_failure(exc)
        session.rollback()
        raise


def fetch_table(
    session: Session, sql: str, params: Mapping[str, object] | None = None
) -> tuple[list[str], list[tuple[object, ...]]]:
    """
    Description:
        Runs one statement and returns its column names in select order and every row as a
        tuple, for the CSV export (REQ-043).

    Parameters:
        session: the request session.
        sql: schema-qualified SQL with :name placeholders; never built from external values.
        params: the bound parameter values, or None.

    Return Values:
        tuple[list[str], list[tuple[object, ...]]]: (column names, rows), rows possibly empty.

    Notes:
        On failure: logs (a database error by class and SQLSTATE only), rolls back the
        session and re-raises (COD-4-04, COD-6-02). The column names are returned even when
        there are no rows, so an empty table still gets its header line.
    """
    try:
        result = session.execute(text(sql), dict(params) if params is not None else {})
        columns = [str(name) for name in result.keys()]
        return columns, [tuple(row) for row in result]
    except Exception as exc:
        _log_sql_failure(exc)
        session.rollback()
        raise


def execute(session: Session, sql: str, params: Mapping[str, object] | None = None) -> int:
    """
    Description:
        Runs one statement that returns no rows the caller needs (UPDATE, DELETE, INSERT
        without RETURNING, or a void function call).

    Parameters:
        session: the request session.
        sql: schema-qualified SQL with :name placeholders; never built from external values.
        params: the bound parameter values, or None.

    Return Values:
        int: the number of rows the statement affected (the DBAPI rowcount).

    Notes:
        On failure: logs (a database error by class and SQLSTATE only), rolls back the
        session and re-raises (COD-4-04, COD-6-02).
    """
    try:
        result = session.execute(text(sql), dict(params) if params is not None else {})
        # Session.execute is typed as Result; a text() statement yields a CursorResult,
        # which carries rowcount.
        return cast("CursorResult[object]", result).rowcount
    except Exception as exc:
        _log_sql_failure(exc)
        session.rollback()
        raise


# --- SQL timing listeners (REQ-068) ---

def _start_query_timer(
    conn: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: object,
    executemany: bool,
) -> None:
    """
    Description:
        SQLAlchemy before_cursor_execute listener: records the start time of a statement on
        its connection.

    Parameters:
        conn: the connection running the statement.
        cursor: the DBAPI cursor (unused).
        statement: the SQL text (unused here).
        parameters: the bound parameters (unused; never logged).
        context: the execution context (unused).
        executemany: whether the statement runs as executemany (unused).

    Return Values:
        None.

    Notes:
        A stack, so a statement issued from inside another (pre-ping) is timed correctly.
    """
    conn.info.setdefault(QUERY_START_KEY, []).append(time.perf_counter())


def _log_query_duration(
    conn: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: object,
    executemany: bool,
) -> None:
    """
    Description:
        SQLAlchemy after_cursor_execute listener: logs the statement's duration and its
        first characters, collapsed to one line, on the hoopster.timing logger at INFO.

    Parameters:
        conn: the connection that ran the statement.
        cursor: the DBAPI cursor (unused).
        statement: the SQL text; only its first TIMING_STATEMENT_CHARS characters are logged.
        parameters: the bound parameters (never logged, COD-4-05).
        context: the execution context (unused).
        executemany: whether the statement ran as executemany (unused).

    Return Values:
        None.

    Notes:
        Logs nothing if no start time was recorded for the statement.
    """
    start_times = conn.info.get(QUERY_START_KEY)
    if not start_times:
        return
    elapsed_ms = (time.perf_counter() - start_times.pop()) * MILLISECONDS_PER_SECOND
    one_line = " ".join(statement.split())[:TIMING_STATEMENT_CHARS]
    timing_logger.info("SQL %.1f ms: %s", elapsed_ms, one_line)
