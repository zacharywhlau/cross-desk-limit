"""§22 PARITY: the standalone prototype and the package must agree.

Runs prototype/check_limit.py --mock and the package check on the SAME reference
inputs and asserts an identical decision, usage and bucket. This is what keeps the
standalone script honest after refactors.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cdl.config import Settings, project_root
from cdl.logic.check import run_check, validate_request
from conftest import EXHAUSTED_COUNTERPARTY, REFERENCE_REQUEST

PROTOTYPE = project_root() / "prototype" / "check_limit.py"

CASES = [
    ("ABCDEFG", "FX", "1 months", "USDHKD", "500000"),
    ("ABCDEFG", "FX", "10 years", "USDTRY", "1000000"),
    ("ABCD", "Gold", "6M", "XAU", "100000"),
    ("ABCDEFG", "IRS", "24 months", "USD", "2000000"),
    ("ABCDEFG", "Equity swaps", "Spot", "USD", "750000"),
    (EXHAUSTED_COUNTERPARTY, "FX", "1M", "USDHKD", "500000"),
]


def run_prototype(tmp_path: Path, arguments: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(PROTOTYPE), "--mock", "--json", *arguments],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = completed.stdout.strip().splitlines()[-1]
    return json.loads(payload)


def test_prototype_imports_nothing_from_the_package() -> None:
    imports = [
        line.strip()
        for line in PROTOTYPE.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]
    assert imports, "the prototype should still import the standard library"
    for statement in imports:
        assert " src" not in statement and "cdl" not in statement, statement
        assert "pandas" not in statement, "pandas is imported lazily on the real path only"


def test_prototype_shows_the_numbered_trace_and_the_paste_point(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(PROTOTYPE), "--mock"],
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for step in range(1, 7):
        assert f"[{step}/6]" in completed.stdout
    assert "PASTE THE COMPANY IMPLEMENTATION HERE" in PROTOTYPE.read_text(encoding="utf-8")
    assert (tmp_path / "prototype_report.txt").is_file()
    assert "[6/6] DECISION" in (tmp_path / "prototype_report.txt").read_text(encoding="utf-8")


def test_prototype_never_prints_the_configured_library(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(PROTOTYPE), "--mock"],
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )
    assert "PASTE_LIBRARY_NAME_HERE" not in completed.stdout
    assert "<LIBRARY>" in completed.stdout


@pytest.mark.parametrize(("counterparty", "product", "tenor", "pair", "notional"), CASES)
def test_parity_on_reference_inputs(
    tmp_path: Path,
    settings: Settings,
    counterparty: str,
    product: str,
    tenor: str,
    pair: str,
    notional: str,
) -> None:
    prototype = run_prototype(tmp_path, [
        "--cpty", counterparty, "--product", product, "--tenor", tenor,
        "--pair", pair, "--notional", notional,
    ])
    request = validate_request(
        username="edmund", counterparty=counterparty, product=product, tenor=tenor,
        pair_or_currency=pair, direction="buy", notional_usd=notional,
    )
    package = run_check(request, settings)

    assert prototype["decision"] == package.decision
    assert prototype["bucket"] == package.affected_bucket
    assert float(prototype["usage"]) == pytest.approx(package.usage)
    assert float(prototype["ffr_weight"]) == pytest.approx(package.ffr.weight)
    assert float(prototype["deal_available_before"]) == pytest.approx(
        package.deal_available_before)
    assert float(prototype["bucket_available_before"]) == pytest.approx(
        package.bucket_available_before)


def test_parity_on_the_reference_case(tmp_path: Path, settings: Settings) -> None:
    prototype = run_prototype(tmp_path, [
        "--cpty", REFERENCE_REQUEST.counterparty,
        "--product", REFERENCE_REQUEST.product,
        "--tenor", REFERENCE_REQUEST.tenor,
        "--pair", REFERENCE_REQUEST.pair_or_currency,
        "--notional", str(int(REFERENCE_REQUEST.notional_usd)),
    ])
    package = run_check(REFERENCE_REQUEST, settings)
    assert prototype["decision"] == package.decision == "Y"
    assert prototype["bucket"] == package.affected_bucket == "Spot-1M"
    assert float(prototype["usage"]) == pytest.approx(509_000.0)
    assert package.usage == pytest.approx(509_000.0)
