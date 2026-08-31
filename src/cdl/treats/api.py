"""The company connector: paste point plus the api fetch path.

Everything around the connector is real: the SQL builder, the payload, the DataFrame
conversion, the row-cap guard and the logging are the same for every table, including
the FFR table. Only the body of `query_to_dataframe` is missing, because it is company
code that exists on the operator's machine and must never be committed.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from ..config import Settings
from ..logging_setup import get_logger, mask_library
from . import sql as sql_builder
from .tabular import Record, dataframe_to_records

#: Present in the placeholder only. Detection looks for the raise below, so leaving
#: this docstring in place after pasting does not confuse `connector_is_pasted`.
_PLACEHOLDER_SENTINEL = "CDL_CONNECTOR_NOT_PASTED"

_logger = get_logger("treats.api")


class ConnectorMissingError(RuntimeError):
    """The company connector has not been pasted into this file yet."""


class ApiFetchError(RuntimeError):
    """The endpoint call failed."""


class RowCapError(ApiFetchError):
    """The result set hit the endpoint row cap, so the rows may be incomplete."""


# ===========================================================================
#  PASTE POINT - STEP 2 OF docs/COMPANY_SETUP.md
#
#  1. Open the company document that describes the tabular query helper.
#  2. Replace the body of `query_to_dataframe` below with that implementation,
#     keeping the signature exactly as it is: (url, payload) -> pandas DataFrame.
#     `payload` is already built for you by sql.build_payload:
#         {"startRow": None, "endRow": None,
#          "libandfile": [{"library": <LIBRARY>, "file": "CKSBLMP"}],
#          "fullSQL": "SELECT * FROM <LIBRARY>.CKSBLMP WHERE ..."}
#  3. Delete the `raise NotImplementedError(...)` line - that line is what
#     `run_check doctor` looks for when it reports "connector pasted: NOT pasted".
#  4. Paste the same implementation into prototype/check_limit.py, which is a
#     standalone file and shares no code with this package.
#
#  DO NOT COMMIT this file once the company implementation is in it: unlike
#  config.ini it is tracked by Git. Keep it local, for example with
#      git update-index --skip-worktree src/cdl/treats/api.py
#  and check `git status` before every commit.
# ===========================================================================
def query_to_dataframe(url: str, payload: dict[str, Any]) -> Any:
    """PASTE THE COMPANY IMPLEMENTATION HERE (see the banner above).

    It must accept the endpoint URL and the payload built by `sql.build_payload`
    and return a pandas DataFrame of the rows.
    """
    raise NotImplementedError(
        f"{_PLACEHOLDER_SENTINEL}: paste the company connector into "
        "src/cdl/treats/api.py (see the banner above that function), then re-run."
    )


def connector_is_pasted() -> bool:
    """True once the placeholder body has been replaced.

    Detection is on the `raise NotImplementedError` statement rather than on any text
    in the docstring, so the operator can keep or drop the docstring freely.
    """
    try:
        source = inspect.getsource(query_to_dataframe)
    except (OSError, TypeError):  # pragma: no cover - source unavailable (frozen build)
        return True
    body = source.split('"""')[-1] if '"""' in source else source
    return "raise NotImplementedError" not in body and _PLACEHOLDER_SENTINEL not in source


def fetch(
    table: str,
    settings: Settings,
    where: str | None = None,
    *,
    start_row: int | None = None,
    end_row: int | None = None,
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
            "into src/cdl/treats/api.py - see the PASTE POINT banner in that file and "
            "docs/COMPANY_SETUP.md"
        )
    payload = sql_builder.build_payload(
        settings.treats.library, table, statement, start_row=start_row, end_row=end_row
    )
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
    cap = settings.treats.max_rows
    bounded = end_row is not None or start_row is not None
    if cap and not bounded and len(rows) >= cap:
        # A truncated read must never produce a Y or an N (§4.7).
        raise RowCapError(
            f"{table} returned {len(rows)} rows, which is the configured endpoint cap "
            f"([treats] max_rows = {cap}); the rows may be incomplete. Narrow the query "
            "with a counterparty filter instead of reading the whole table"
        )
    return rows, statement
