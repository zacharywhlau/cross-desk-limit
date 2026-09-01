"""The api path around the company connector: paste detection and the row cap.

The connector body itself is company code that never lives in this repository, so the
tests substitute a fake one and check everything the tool does around it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from cdl import constants
from cdl.config import Settings
from cdl.treats import api
from cdl.treats import source as source_module

pd = pytest.importorskip("pandas")


@pytest.fixture
def api_settings(settings: Settings) -> Settings:
    """Every table on api, with placeholder connection values."""
    return replace(
        settings,
        treats=replace(
            settings.treats,
            url="http://endpoint.example/query",
            library="MYLIB",
            ttcpipp=constants.SOURCE_API,
            cksblmp=constants.SOURCE_API,
            ckovlmp=constants.SOURCE_API,
            max_rows=10,
        ),
    )


def rows_frame(count: int):
    return pd.DataFrame(
        [{"CFCPTY": f"ABC{index:04d}", "CFSLTT": "1000"} for index in range(count)]
    )


def test_the_placeholder_is_reported_as_not_pasted() -> None:
    assert api.connector_is_pasted() is False


def test_a_pasted_connector_is_detected_even_with_the_docstring_left_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trap that cost debugging time: detection must not key off the docstring."""

    def query_to_dataframe(url, payload):  # noqa: ANN001, ANN202 - mirrors the paste point
        """PASTE THE COMPANY IMPLEMENTATION HERE.

        The operator keeps this docstring and replaces only the body.
        """
        return rows_frame(1)

    monkeypatch.setattr(api, "query_to_dataframe", query_to_dataframe)
    assert api.connector_is_pasted() is True


def test_a_connector_that_still_raises_is_reported_as_not_pasted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def query_to_dataframe(url, payload):  # noqa: ANN001, ANN202
        """Half-done edit: docstring changed, body untouched."""
        raise NotImplementedError("still to do")

    monkeypatch.setattr(api, "query_to_dataframe", query_to_dataframe)
    assert api.connector_is_pasted() is False


def test_fetch_explains_how_to_paste_the_connector(api_settings: Settings) -> None:
    with pytest.raises(api.ConnectorMissingError) as error:
        api.fetch(constants.TABLE_LIMITS, api_settings)
    message = str(error.value)
    assert "src/cdl/treats/api.py" in message
    assert "PASTE POINT" in message or "paste" in message


def test_a_result_at_the_row_cap_is_refused_rather_than_trusted(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint caps a result set; a capped read must not become a Y or an N."""
    monkeypatch.setattr(api, "query_to_dataframe", lambda url, payload: rows_frame(10))
    with pytest.raises(api.RowCapError) as error:
        api.fetch(constants.TABLE_LIMITS, api_settings)
    assert "max_rows" in str(error.value)
    assert "10 rows" in str(error.value)


def test_a_result_below_the_cap_is_returned(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "query_to_dataframe", lambda url, payload: rows_frame(9))
    rows, statement = api.fetch(constants.TABLE_LIMITS, api_settings)
    assert len(rows) == 9
    assert statement == "SELECT * FROM MYLIB.CKSBLMP"


def test_a_bounded_read_is_allowed_to_return_exactly_its_bound(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sample asked for 10 rows, so 10 rows is the answer, not a truncation."""
    seen: dict[str, object] = {}

    def connector(url, payload):  # noqa: ANN001, ANN202
        seen.update(payload)
        return rows_frame(10)

    monkeypatch.setattr(api, "query_to_dataframe", connector)
    rows, _ = api.fetch(constants.TABLE_LIMITS, api_settings, end_row=10)
    assert len(rows) == 10
    assert seen["endRow"] == 10


def test_a_decision_read_is_narrowed_by_counterparty(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No hardcoded counterparty list anywhere: the WHERE is built from the request."""
    from cdl.logic.check import run_check
    from conftest import REFERENCE_REQUEST

    statements: list[str] = []

    def connector(url, payload):  # noqa: ANN001, ANN202
        statements.append(str(payload["fullSQL"]))
        table = str(payload["libandfile"][0]["file"])
        if table == constants.TABLE_COUNTERPARTY:
            return pd.DataFrame([{"XJCPAC": "ABCDEFG", "XJPRAC": "ABCDGRP"}])
        if table == constants.TABLE_LIMITS:
            row = {
                "CFCPTY": "ABCDEFG",
                "CFSLMT": "FX 01",
                "CFSLTT": "20000000",
                "CFSO05": "1000000",
            }
            row.update({f"CFSL{slot:02d}": "20000000" for slot in range(1, 15)})
            return pd.DataFrame([row])
        return pd.DataFrame([{"CICPTY": "ABCDEFG", "CIRFMG": "SYNTHETIC MOCK TEXT"}])

    monkeypatch.setattr(api, "query_to_dataframe", connector)
    # FFR stays on mock, as on the operator's PC until CKBLOTP is confirmed.
    result = run_check(REFERENCE_REQUEST, api_settings)

    assert result.decision == constants.DECISION_YES
    assert statements, "the connector was never called"
    assert all("WHERE" in statement for statement in statements), statements
    limits = [s for s in statements if constants.TABLE_LIMITS in s]
    agreements = [s for s in statements if constants.TABLE_AGREEMENT in s]
    # One IN (...) read per table covers the submitted counterparty and its parents.
    assert len(limits) == 1 and "CFCPTY IN ('ABCDEFG','ABCDGRP')" in limits[0]
    assert len(agreements) == 1 and "CICPTY IN ('ABCDEFG','ABCDGRP')" in agreements[0]


def test_source_layer_reports_the_table_and_mode_on_failure(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def connector(url, payload):  # noqa: ANN001, ANN202
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(api, "query_to_dataframe", connector)
    with pytest.raises(source_module.SourceError) as error:
        source_module.fetch_table(constants.TABLE_LIMITS, api_settings)
    assert constants.TABLE_LIMITS in str(error.value)
    assert "api" in str(error.value)


def test_the_url_never_reaches_the_error_message(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def connector(url, payload):  # noqa: ANN001, ANN202
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(api, "query_to_dataframe", connector)
    with pytest.raises(source_module.SourceError) as error:
        source_module.fetch_table(constants.TABLE_LIMITS, api_settings)
    assert "endpoint.example" not in str(error.value)
