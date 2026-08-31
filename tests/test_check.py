"""§22: mock end-to-end Y and N, plus the ERROR path when a source raises."""

from __future__ import annotations

import pytest

from cdl import constants
from cdl.config import Settings
from cdl.logic import check as check_module
from cdl.logic.check import ValidationError, run_check, validate_request
from cdl.store.db import HoldsStore
from cdl.treats import source as source_module
from conftest import EXHAUSTED_COUNTERPARTY, REFERENCE_REQUEST


def test_reference_case_is_a_yes(settings: Settings) -> None:
    result = run_check(REFERENCE_REQUEST, settings)
    assert result.decision == constants.DECISION_YES
    assert result.affected_bucket == "SPT-1M"
    assert result.usage == pytest.approx(509_000.0)
    assert result.ffr is not None
    assert result.ffr.currency_class == "Low"
    assert result.ffr.table_name == "FFR_FX_LOW"
    assert result.deal_available_before == pytest.approx(16_500_000)
    assert result.deal_available_after == pytest.approx(15_991_000)
    assert result.bucket_available_before == pytest.approx(16_500_000)
    assert result.bucket_available_after == pytest.approx(15_991_000)
    assert result.sources[constants.TABLE_LIMITS] == constants.SOURCE_MOCK


def test_a_long_dated_deal_is_rejected_when_the_period_has_no_limit(
    settings: Settings
) -> None:
    """The mock FX counterparty has no limit beyond five years, as on the desk screen."""
    request = validate_request(
        username="edmund", counterparty="ABCDEFG", product="FX", tenor="10 years",
        pair_or_currency="USDHKD", direction="buy", notional_usd=100_000,
    )
    result = run_check(request, settings)
    assert result.decision == constants.DECISION_NO
    assert result.affected_bucket == "7Y-10Y"
    assert result.bucket_available_before == 0.0
    assert "period 7Y-10Y" in result.message


def test_chain_is_reference_only(settings: Settings) -> None:
    result = run_check(REFERENCE_REQUEST, settings)
    assert [node.counterparty for node in result.chain] == ["ABCDEFG", "ABCDGRP"]
    submitted, parent = result.chain
    assert submitted.is_submitted and not parent.is_submitted
    assert parent.surface is not None
    assert parent.surface.deal_limit == pytest.approx(60_000_000)
    assert "SYNTHETIC MOCK TEXT" in submitted.agreement_text
    # The parent has far more room, but the decision follows the submitted name only.
    exhausted = run_check(
        check_module.validate_request(
            username="edmund",
            counterparty=EXHAUSTED_COUNTERPARTY,
            product="FX",
            tenor="1 months",
            pair_or_currency="USDHKD",
            direction="buy",
            notional_usd=500_000,
        ),
        settings,
    )
    assert exhausted.decision == constants.DECISION_NO
    assert exhausted.chain[1].counterparty == "ABCDGRP"


def test_exhausted_counterparty_is_a_no(settings: Settings) -> None:
    request = validate_request(
        username="alice",
        counterparty=EXHAUSTED_COUNTERPARTY,
        product="FX",
        tenor="1M",
        pair_or_currency="USDHKD",
        direction="sell",
        notional_usd="500000",
    )
    result = run_check(request, settings)
    assert result.decision == constants.DECISION_NO
    assert "Insufficient limit" in result.message
    assert "Hard reject" in result.message


def test_four_character_counterparty_works(settings: Settings) -> None:
    request = validate_request(
        username="edmund", counterparty="ABCD", product="Gold", tenor="6M",
        pair_or_currency="XAU", direction="buy", notional_usd=100_000,
    )
    result = run_check(request, settings)
    assert result.decision == constants.DECISION_YES
    assert result.affected_bucket == "3M-6M"
    assert result.chain[0].parent is None
    assert len(result.chain) == 1


@pytest.mark.parametrize("product", constants.PRODUCTS)
def test_every_product_is_reachable_and_computable(product: str, settings: Settings) -> None:
    currency = "USDHKD" if product == "FX" else constants.NON_FX_CURRENCY[product]
    request = validate_request(
        username="edmund", counterparty="ABCDEFG", product=product, tenor="1 months",
        pair_or_currency=currency, direction="buy", notional_usd=100_000,
    )
    result = run_check(request, settings)
    assert result.decision == constants.DECISION_YES
    assert result.surface is not None
    assert result.surface.limit_type == constants.LIMIT_TYPE_BY_PRODUCT[product]
    assert result.usage > 100_000


def test_a_yes_creates_a_hold_and_the_next_check_sees_it(settings: Settings) -> None:
    store = HoldsStore(settings)
    first = run_check(REFERENCE_REQUEST, settings, store)
    assert first.hold_id is not None
    second = run_check(REFERENCE_REQUEST, settings, store)
    assert second.surface is not None
    assert second.surface.holds_usage == pytest.approx(509_000.0)
    assert second.deal_available_before == pytest.approx(16_500_000 - 509_000)
    assert len(second.active_holds) == 1


def test_a_no_creates_no_hold(settings: Settings) -> None:
    store = HoldsStore(settings)
    request = validate_request(
        username="alice", counterparty=EXHAUSTED_COUNTERPARTY, product="FX",
        tenor="1 months", pair_or_currency="USDHKD", direction="buy", notional_usd=500_000,
    )
    result = run_check(request, settings, store)
    assert result.decision == constants.DECISION_NO
    assert result.hold_id is None
    assert result.check_id is not None
    assert store.active_holds(EXHAUSTED_COUNTERPARTY, "FX") == []


def test_error_when_a_required_source_raises(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = source_module.fetch_table

    def failing(table: str, *args, **kwargs):
        if table == constants.TABLE_LIMITS:
            raise source_module.SourceError("endpoint unavailable")
        return original(table, *args, **kwargs)

    monkeypatch.setattr(check_module.source_module, "fetch_table", failing)
    result = run_check(REFERENCE_REQUEST, settings)
    assert result.decision == constants.DECISION_ERROR
    assert result.failed_table == constants.TABLE_LIMITS
    assert result.failed_source == constants.SOURCE_MOCK
    assert constants.TABLE_LIMITS in result.message
    assert "mock mode" in result.message
    assert result.hold_id is None


def test_error_when_the_ffr_grid_is_missing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdl.logic import ffr as ffr_module

    def failing(*args, **kwargs):
        raise ffr_module.FfrError("FFR_FX_LOW.csv not found")

    monkeypatch.setattr(check_module, "lookup_ffr", failing)
    result = run_check(REFERENCE_REQUEST, settings)
    assert result.decision == constants.DECISION_ERROR
    assert "FFR" in result.message
    assert settings.ffr.table in result.message


def test_error_when_the_counterparty_is_unknown(settings: Settings) -> None:
    request = validate_request(
        username="edmund", counterparty="WXYZ", product="FX", tenor="1 months",
        pair_or_currency="USDHKD", direction="buy", notional_usd=500_000,
    )
    result = run_check(request, settings)
    assert result.decision == constants.DECISION_ERROR
    assert "WXYZ" in result.message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("username", ""),
        ("counterparty", "ABCDE"),
        ("product", "crude oil"),
        ("tenor", "1 fortnight"),
        ("pair_or_currency", ""),
        ("direction", "hold"),
        ("notional_usd", "0"),
        ("notional_usd", "-1"),
        ("notional_usd", "abc"),
    ],
)
def test_validation_rejects_bad_input_before_any_call(field: str, value: str) -> None:
    fields = {
        "username": "edmund",
        "counterparty": "ABCDEFG",
        "product": "FX",
        "tenor": "1 months",
        "pair_or_currency": "USDHKD",
        "direction": "buy",
        "notional_usd": 500_000,
    }
    fields[field] = value
    with pytest.raises(ValidationError):
        validate_request(**fields)


def test_direction_is_stored_but_not_used_in_the_formula(settings: Settings) -> None:
    buy = run_check(REFERENCE_REQUEST, settings)
    sell = run_check(
        validate_request(
            username="edmund", counterparty="ABCDEFG", product="FX", tenor="1 months",
            pair_or_currency="USDHKD", direction="sell", notional_usd=500_000,
        ),
        settings,
    )
    assert buy.usage == sell.usage
    assert sell.request.direction == "sell"
