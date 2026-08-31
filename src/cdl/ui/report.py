"""The breakdown as plain text and as report.html. No business logic here."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from .. import constants
from ..logic import numbers
from ..models import CheckRecord, CheckResult, Hold

DEFAULT_REPORT_NAME = "report.html"


def decision_headline(result: CheckResult) -> str:
    if result.is_error:
        return "ERROR"
    return result.decision


def text_report(
    result: CheckResult,
    *,
    peers: Sequence[tuple[Hold, float]] = (),
    history: Sequence[CheckRecord] = (),
    now: datetime | None = None,
) -> str:
    """The whole breakdown as lines, used by the CLI and by the tkinter window."""
    moment = now or datetime.now()
    request = result.request
    lines: list[str] = []
    lines.append(f"cross-desk-limit check  {moment:%Y-%m-%d %H:%M:%S}")
    lines.append("")
    lines.append(f"user            : {request.username}")
    lines.append(f"counterparty    : {request.counterparty}")
    lines.append(f"product         : {request.product}")
    lines.append(f"tenor           : {request.tenor}  (bucket {result.affected_bucket or '-'})")
    lines.append(f"pair / currency : {request.pair_or_currency}")
    lines.append(f"direction       : {request.direction}  (stored, not used in the formula)")
    lines.append(f"notional USD    : {numbers.amount(request.notional_usd)}")
    lines.append("")
    lines.append(f"DECISION        : {decision_headline(result)}")
    lines.append(f"message         : {result.message}")
    if result.ffr is not None:
        ffr = result.ffr
        lines.append(
            f"FFR             : table={ffr.table_name} source={ffr.source_label} "
            f"column={ffr.weight_column} weight={numbers.percent(ffr.weight)}"
        )
        lines.append(
            f"FFR selection   : {ffr.filter_description}"
            + (f"  class={ffr.currency_class}" if ffr.currency_class else "")
        )
    lines.append(f"usage           : {numbers.amount(result.usage)} "
                 f"({numbers.millions(result.usage)})")
    lines.append("sources         : " + ", ".join(
        f"{table}={mode}" for table, mode in sorted(result.sources.items())))
    if result.check_id is not None:
        lines.append(f"history id      : {result.check_id}")
    if result.hold_id is not None:
        lines.append(f"hold id         : {result.hold_id} (temporary hold, not a booking)")

    surface = result.surface
    if surface is not None:
        lines.append("")
        lines.append(f"Breakdown - {surface.counterparty} {surface.product} "
                     f"(limit type {surface.limit_type})")
        lines.append(f"  deal limit        : {numbers.millions(surface.deal_limit)}")
        lines.append(f"  utilisation       : {numbers.millions(surface.utilisation)}")
        lines.append(f"  active holds      : {numbers.millions(surface.holds_usage)}")
        lines.append(f"  available before  : {numbers.millions(result.deal_available_before)}")
        lines.append(f"  this request usage: {numbers.millions(result.usage)}")
        lines.append(f"  available after   : {numbers.millions(result.deal_available_after)}")
        lines.append("  time periods (the limit ladder: a deal consumes every shorter period):")
        lines.append(
            f"    {'period':<9} {'limit':>12} {'cash risk':>12} {'holds':>12} "
            f"{'risk >= here':>13} {'available':>12}"
        )
        for bucket in surface.buckets:
            marker = " <- this deal" if bucket.bucket == result.affected_bucket else ""
            lines.append(
                f"    {bucket.bucket:<9} {numbers.millions(bucket.limit):>12} "
                f"{numbers.millions(bucket.occupied):>12} "
                f"{numbers.millions(bucket.holds_usage):>12} "
                f"{numbers.millions(bucket.reverse_cumulative):>13} "
                f"{numbers.millions(bucket.available):>12}{marker}"
            )

    if result.chain:
        lines.append("")
        lines.append("Counterparty chain (reference only - never decides Y/N)")
        for node in result.chain:
            label = "submitted" if node.is_submitted else f"parent depth {node.depth}"
            node_surface = node.surface
            figures = (
                f"limit={numbers.millions(node_surface.deal_limit)} "
                f"utilisation={numbers.millions(node_surface.utilisation)} "
                f"holds={numbers.millions(node_surface.holds_usage)} "
                f"available={numbers.millions(node_surface.available)}"
                if node_surface is not None else "no limit row"
            )
            lines.append(f"  {node.counterparty} ({label}) parent="
                         f"{node.parent or '(none)'}  {figures}")
            if node.agreement_text:
                lines.append(f"    agreement: {node.agreement_text}")

    if peers:
        lines.append("")
        lines.append("Traders who have asked (active holds)")
        for hold, minutes in peers:
            lines.append(
                f"  hold {hold.id:<4} {hold.username:<12} {hold.tenor:<10} "
                f"notional={numbers.millions(hold.notional_usd)} "
                f"usage={numbers.millions(hold.usage)} "
                f"bucket={hold.affected_bucket:<8} {minutes:.0f} min left"
            )

    if history:
        lines.append("")
        lines.append("Today's checks")
        for record in history:
            lines.append(
                f"  {record.created_at:%H:%M:%S} {record.decision:<5} "
                f"{record.username:<12} {record.counterparty:<8} {record.product:<13} "
                f"{record.tenor:<10} usage={numbers.millions(record.usage)}"
            )
    return "\n".join(lines) + "\n"


def _html_rows(rows: Iterable[Sequence[str]], *, highlight: str | None = None) -> str:
    out: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        css = " class=\"hit\"" if highlight is not None and str(row[0]) == highlight else ""
        out.append(f"<tr{css}>{cells}</tr>")
    return "\n".join(out)


def html_report(
    result: CheckResult,
    *,
    peers: Sequence[tuple[Hold, float]] = (),
    history: Sequence[CheckRecord] = (),
    now: datetime | None = None,
) -> str:
    """report.html - the same breakdown, readable in a browser."""
    moment = now or datetime.now()
    request = result.request
    colour = {
        constants.DECISION_YES: "#1a7f37",
        constants.DECISION_NO: "#c02020",
    }.get(result.decision, "#8a6d00")

    surface = result.surface
    bucket_rows = _html_rows(
        [
            (
                bucket.bucket,
                numbers.millions(bucket.limit),
                numbers.millions(bucket.occupied),
                numbers.millions(bucket.holds_usage),
                numbers.millions(bucket.reverse_cumulative),
                numbers.millions(bucket.available),
            )
            for bucket in (surface.buckets if surface else ())
        ],
        highlight=result.affected_bucket,
    )
    chain_rows = _html_rows(
        [
            (
                node.counterparty,
                node.parent or "(none)",
                numbers.millions(node.surface.deal_limit) if node.surface else "-",
                numbers.millions(node.surface.utilisation) if node.surface else "-",
                numbers.millions(node.surface.holds_usage) if node.surface else "-",
                numbers.millions(node.surface.available) if node.surface else "-",
                node.agreement_text,
            )
            for node in result.chain
        ]
    )
    peer_rows = _html_rows(
        [
            (
                str(hold.id),
                hold.username,
                hold.tenor,
                hold.affected_bucket,
                numbers.millions(hold.notional_usd),
                numbers.millions(hold.usage),
                f"{minutes:.0f}",
            )
            for hold, minutes in peers
        ]
    )
    history_rows = _html_rows(
        [
            (
                f"{record.created_at:%H:%M:%S}",
                record.decision,
                record.username,
                record.counterparty,
                record.product,
                record.tenor,
                numbers.millions(record.usage),
            )
            for record in history
        ]
    )
    ffr = result.ffr
    if ffr is None:
        ffr_line = "not resolved"
    else:
        ffr_line = (
            f"table {html.escape(ffr.table_name)} / source {html.escape(ffr.source_label)} / "
            f"column {html.escape(ffr.weight_column)} / weight {numbers.percent(ffr.weight)}"
        )
        if ffr.currency_class:
            ffr_line += f" / class {html.escape(str(ffr.currency_class))}"
    sources_line = ", ".join(
        f"{html.escape(table)}={html.escape(mode)}"
        for table, mode in sorted(result.sources.items())
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>cross-desk-limit check {html.escape(request.counterparty)}</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
 h1 {{ font-size: 20px; }}
 h2 {{ font-size: 15px; margin-top: 24px; }}
 .decision {{ font-size: 48px; font-weight: bold; color: {colour}; }}
 table {{ border-collapse: collapse; margin-top: 6px; }}
 th, td {{ border: 1px solid #ccc; padding: 4px 8px; font-size: 13px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 tr.hit {{ background: #fff6cc; }}
 .note {{ color: #555; font-size: 12px; }}
 dl {{ display: grid; grid-template-columns: 180px auto; gap: 2px 12px; font-size: 13px; }}
 dt {{ color: #555; }}
</style>
</head>
<body>
<h1>cross-desk-limit &mdash; counterparty limit check</h1>
<p class="note">{moment:%Y-%m-%d %H:%M:%S} &middot; a temporary hold is a soft reservation,
not a booking.</p>
<div class="decision">{html.escape(decision_headline(result))}</div>
<p>{html.escape(result.message)}</p>
<dl>
  <dt>user</dt><dd>{html.escape(request.username)}</dd>
  <dt>counterparty</dt><dd>{html.escape(request.counterparty)}</dd>
  <dt>product</dt><dd>{html.escape(request.product)}</dd>
  <dt>tenor</dt><dd>{html.escape(request.tenor)} (bucket
      {html.escape(result.affected_bucket or '-')})</dd>
  <dt>pair / currency</dt><dd>{html.escape(request.pair_or_currency)}</dd>
  <dt>direction</dt><dd>{html.escape(request.direction)} (stored, not used in the formula)</dd>
  <dt>notional USD</dt><dd>{numbers.amount(request.notional_usd)}</dd>
  <dt>usage</dt><dd>{numbers.amount(result.usage)} ({numbers.millions(result.usage)})</dd>
  <dt>FFR</dt><dd>{ffr_line}</dd>
  <dt>sources</dt><dd>{sources_line}</dd>
  <dt>hold</dt><dd>{result.hold_id if result.hold_id is not None else "none"}</dd>
</dl>

<h2>Deal limit</h2>
<dl>
  <dt>limit</dt><dd>{numbers.millions(surface.deal_limit) if surface else '-'}</dd>
  <dt>utilisation</dt><dd>{numbers.millions(surface.utilisation) if surface else '-'}</dd>
  <dt>active holds</dt><dd>{numbers.millions(surface.holds_usage) if surface else '-'}</dd>
  <dt>available before</dt><dd>{numbers.millions(result.deal_available_before)}</dd>
  <dt>available after</dt><dd>{numbers.millions(result.deal_available_after)}</dd>
</dl>

<h2>Time periods <span class="note">(the limit ladder: a deal consumes every shorter
period, so available is the running minimum)</span></h2>
<table>
<tr><th>period</th><th>limit</th><th>cash risk</th><th>holds</th>
<th>risk from here on</th><th>available</th></tr>
{bucket_rows}
</table>

<h2>Counterparty chain <span class="note">(reference only &mdash; never decides Y/N)</span></h2>
<table>
<tr><th>counterparty</th><th>parent</th><th>limit</th><th>utilisation</th><th>holds</th>
<th>available</th><th>agreement</th></tr>
{chain_rows}
</table>

<h2>Traders who have asked</h2>
<table>
<tr><th>hold</th><th>user</th><th>tenor</th><th>bucket</th><th>notional</th><th>usage</th>
<th>min left</th></tr>
{peer_rows}
</table>

<h2>Today's checks</h2>
<table>
<tr><th>time</th><th>decision</th><th>user</th><th>counterparty</th><th>product</th>
<th>tenor</th><th>usage</th></tr>
{history_rows}
</table>
</body>
</html>
"""


def write_html_report(
    result: CheckResult,
    path: str | Path = DEFAULT_REPORT_NAME,
    *,
    peers: Sequence[tuple[Hold, float]] = (),
    history: Sequence[CheckRecord] = (),
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_report(result, peers=peers, history=history), encoding="utf-8")
    return target
