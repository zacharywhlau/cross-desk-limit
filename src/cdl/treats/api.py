"""The company connector: paste point plus the api fetch path.

The body of `query_to_dataframe` is company code that exists only on the operator's
machine. In this repository it is a placeholder that raises NotImplementedError.
Everything around it is real: the SQL builder, the payload, the DataFrame conversion
and the logging are the same for every table, including the FFR table.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from ..config import Settings
from ..logging_setup import get_logger, mask_library
from . import sql as sql_builder
from .tabular import Record, dataframe_to_records

_PLACEHOLDER_MARKER = "PASTE THE COMPANY IMPLEMENTATION HERE"

_logger = get_logger("treats.api")


class ConnectorMissingError(RuntimeError):
    """The company connector has not been pasted into this file yet."""


class ApiFetchError(RuntimeError):
    """The endpoint call failed."""


def query_to_dataframe(url: str, payload: dict[str, Any]) -> Any:
    """PASTE THE COMPANY IMPLEMENTATION HERE.

    It must accept the endpoint URL and the payload built by `sql.build_payload`
    and return a pandas DataFrame of the rows.
    """
    raise NotImplementedError(_PLACEHOLDER_MARKER + " - paste the company connector, then re-run.")


def connector_is_pasted() -> bool:
    """True once the placeholder body has been replaced."""
    try:
        source = inspect.getsource(query_to_dataframe)
    except (OSError, TypeError):  # pragma: no cover - source unavailable (frozen build)
        return True
    return _PLACEHOLDER_MARKER not in source


def fetch(
    table: str,
    settings: Settings,
    where: str | None = None,
) -> tuple[list[Record], str]:
    """Query one table through the connector. Returns (rows, statement)."""
    if not settings.treats.library:
        raise ApiFetchError("[treats] library is not set in config.ini")
    if not settings.treats.url:
        raise ApiFetchError("[treats] url is not set in config.ini")
    statement = sql_builder.build_select(settings.treats.library, table, where)
    masked = mask_library(statement, settings.treats.library)
    if not connector_is_pasted():
        raise ConnectorMissingError(
            f"cannot read {table} in api mode: the company connector has not been pasted "
            "into src/cdl/treats/api.py (see docs/COMPANY_SETUP.md)"
        )
    payload = sql_builder.build_payload(settings.treats.library, table, statement)
    started = time.monotonic()
    try:
        frame = query_to_dataframe(url=settings.treats.url, payload=payload)
        rows = dataframe_to_records(frame)
    except NotImplementedError as error:
        raise ConnectorMissingError(
            f"cannot read {table} in api mode: {error}"
        ) from error
    except Exception as error:
        _logger.error("api fetch failed table=%s sql=%s error=%s", table, masked, error)
        raise ApiFetchError(f"{table} query failed: {type(error).__name__}: {error}") from error
    elapsed_ms = (time.monotonic() - started) * 1000.0
    _logger.info(
        "fetch source=api table=%s rows=%d elapsed_ms=%.1f sql=%s",
        table, len(rows), elapsed_ms, masked,
    )
    return rows, statement
