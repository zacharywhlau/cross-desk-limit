"""M4 - the tkinter window. One process, no server, no port.

Sections top to bottom: login, input, decision, breakdown, counterparty chain,
traders who have asked, today's history. No business logic lives here: the window
calls the same `run_check` the CLI calls.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Sequence

from .. import constants
from ..config import Settings, load_settings
from ..logging_setup import get_logger, log_startup, setup_logging
from ..logic import numbers
from ..logic.check import ValidationError, run_check, validate_request
from ..models import CheckRecord, CheckResult, Hold
from ..store.db import HoldsStore, StoreError

_logger = get_logger("ui")

WINDOW_TITLE = "cross-desk-limit - counterparty limit check"
PAD = 8


class LimitCheckApp(ttk.Frame):
    """The single window. Every widget group is built by its own small method."""

    def __init__(self, master: tk.Misc, settings: Settings, store: HoldsStore) -> None:
        super().__init__(master, padding=PAD)
        self.settings = settings
        self.store = store
        self.username = tk.StringVar()
        self.counterparty = tk.StringVar(value="ABCDEFG")
        self.product = tk.StringVar(value=constants.PRODUCT_FX)
        self.tenor = tk.StringVar(value="1 months")
        self.pair = tk.StringVar(value="USDHKD")
        self.direction = tk.StringVar(value=constants.DIRECTIONS[0])
        self.notional = tk.StringVar(value="500000")
        self.logged_in_user: str | None = None
        self.last_result: CheckResult | None = None

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        row = 0
        row = self._build_login(row)
        row = self._build_input(row)
        row = self._build_decision(row)
        row = self._build_breakdown(row)
        row = self._build_chain(row)
        row = self._build_peers(row)
        row = self._build_history(row)
        self.rowconfigure(row - 1, weight=1)
        self.refresh_history()

    # -- 1. login ---------------------------------------------------------
    def _build_login(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="1. Login", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        ttk.Label(frame, text="Username").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.username, width=20)
        entry.grid(row=0, column=1, padx=(PAD, PAD))
        entry.bind("<Return>", lambda _event: self.on_login())
        ttk.Button(frame, text="Set user", command=self.on_login).grid(row=0, column=2)
        self.login_label = ttk.Label(frame, text="not logged in", foreground="#8a6d00")
        self.login_label.grid(row=0, column=3, padx=(PAD, 0), sticky="w")
        ttk.Label(
            frame,
            text="No password: identity is the username you type.",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        return row + 1

    # -- 2. input ---------------------------------------------------------
    def _build_input(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="2. Proposed deal", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        fields = (
            ("Counterparty (4 or 7)", ttk.Entry(frame, textvariable=self.counterparty, width=12)),
            ("Product", ttk.Combobox(frame, textvariable=self.product, width=14,
                                     values=list(constants.PRODUCTS), state="readonly")),
            ("Tenor", ttk.Combobox(frame, textvariable=self.tenor, width=14,
                                   values=list(constants.TENOR_GRID))),
            ("Pair / currency", ttk.Combobox(frame, textvariable=self.pair, width=12,
                                             values=list(constants.FX_PAIRS))),
            ("Direction", ttk.Combobox(frame, textvariable=self.direction, width=8,
                                       values=list(constants.DIRECTIONS), state="readonly")),
            ("Notional USD", ttk.Entry(frame, textvariable=self.notional, width=16)),
        )
        for index, (label, widget) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=0, column=index, sticky="w", padx=(0, PAD))
            widget.grid(row=1, column=index, sticky="w", padx=(0, PAD))
        self.submit_button = ttk.Button(frame, text="Submit", command=self.on_submit)
        self.submit_button.grid(row=1, column=len(fields), padx=(PAD, 0))
        return row + 1

    # -- 3. decision ------------------------------------------------------
    def _build_decision(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="3. Decision", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        frame.columnconfigure(1, weight=1)
        self.decision_label = tk.Label(frame, text="-", font=("Segoe UI", 40, "bold"),
                                       width=6, fg="#555")
        self.decision_label.grid(row=0, column=0, rowspan=2, sticky="w")
        self.message_label = ttk.Label(frame, text="Type a deal and press Submit.",
                                       wraplength=900, justify="left")
        self.message_label.grid(row=0, column=1, sticky="w")
        self.detail_label = ttk.Label(frame, text="", justify="left", foreground="#333")
        self.detail_label.grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.sources_label = ttk.Label(frame, text="", foreground="#555")
        self.sources_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return row + 1

    # -- 4. breakdown -----------------------------------------------------
    def _build_breakdown(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="4. Breakdown", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        frame.columnconfigure(1, weight=1)
        self.deal_label = ttk.Label(frame, text="deal limit: -", justify="left")
        self.deal_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.bucket_table = self._make_table(
            frame, ("bucket", "limit", "occupied", "holds", "available"), height=5)
        self.bucket_table.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        return row + 1

    # -- 5. counterparty chain -------------------------------------------
    def _build_chain(self, row: int) -> int:
        frame = ttk.LabelFrame(
            self, text="5. Counterparty chain (reference only - never decides Y/N)", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        frame.columnconfigure(0, weight=1)
        self.chain_table = self._make_table(
            frame,
            ("counterparty", "parent", "limit", "utilisation", "holds", "available", "agreement"),
            height=4,
            widths={"agreement": 520},
        )
        self.chain_table.grid(row=0, column=0, sticky="ew")
        return row + 1

    # -- 6. traders who have asked ---------------------------------------
    def _build_peers(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="6. Traders who have asked", padding=PAD)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        frame.columnconfigure(0, weight=1)
        self.peer_table = self._make_table(
            frame,
            ("hold", "user", "tenor", "bucket", "notional", "usage", "min left"),
            height=4,
        )
        self.peer_table.grid(row=0, column=0, sticky="ew")
        self.release_button = ttk.Button(frame, text="Release selected hold",
                                         command=self.on_release, state="disabled")
        self.release_button.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.peer_table.bind("<<TreeviewSelect>>", lambda _event: self._update_release_button())
        return row + 1

    # -- 7. today's history ----------------------------------------------
    def _build_history(self, row: int) -> int:
        frame = ttk.LabelFrame(self, text="7. Today's checks", padding=PAD)
        frame.grid(row=row, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.history_table = self._make_table(
            frame,
            ("time", "decision", "user", "counterparty", "product", "tenor", "usage"),
            height=6,
        )
        self.history_table.grid(row=0, column=0, sticky="nsew")
        return row + 1

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _make_table(
        parent: tk.Misc,
        columns: Sequence[str],
        *,
        height: int,
        widths: dict[str, int] | None = None,
    ) -> ttk.Treeview:
        table = ttk.Treeview(parent, columns=list(columns), show="headings", height=height)
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=(widths or {}).get(column, 110), anchor="w",
                         stretch=column in ("agreement",))
        return table

    @staticmethod
    def _fill(table: ttk.Treeview, rows: Sequence[Sequence[object]]) -> None:
        table.delete(*table.get_children())
        for values in rows:
            table.insert("", "end", values=[str(value) for value in values])

    # -- actions ----------------------------------------------------------
    def on_login(self) -> None:
        name = self.username.get().strip()
        if not name:
            messagebox.showwarning("Login", "Type a username first.")
            return
        self.logged_in_user = name
        self.login_label.configure(text=f"logged in as {name}", foreground="#1a7f37")
        self._update_release_button()
        self.refresh_history()

    def on_submit(self) -> None:
        if not self.logged_in_user:
            messagebox.showwarning("Login", "Set a username before submitting.")
            return
        try:
            request = validate_request(
                username=self.logged_in_user,
                counterparty=self.counterparty.get(),
                product=self.product.get(),
                tenor=self.tenor.get(),
                pair_or_currency=self.pair.get(),
                direction=self.direction.get(),
                notional_usd=self.notional.get(),
            )
        except ValidationError as error:
            self._show_error(str(error))
            return

        self.submit_button.configure(state="disabled")
        try:
            result = run_check(request, self.settings, self.store)
        finally:
            self.submit_button.configure(state="normal")
        self.last_result = result
        if result.is_error:
            try:
                self.store.record_error(request, result.message,
                                        affected_bucket=result.affected_bucket)
            except StoreError as error:
                _logger.warning("could not record the ERROR outcome: %s", error)
        self._show_result(result)
        self.refresh_peers(request.counterparty, request.product)
        self.refresh_history()

    def on_release(self) -> None:
        selection = self.peer_table.selection()
        if not selection or not self.logged_in_user:
            return
        hold_id = int(self.peer_table.item(selection[0], "values")[0])
        try:
            hold = self.store.release(hold_id, self.logged_in_user)
        except StoreError as error:
            messagebox.showerror("Release", str(error))
            return
        messagebox.showinfo("Release", f"Hold {hold.id} released.")
        self.refresh_peers(hold.counterparty, hold.product)
        self.refresh_history()

    # -- rendering --------------------------------------------------------
    def _show_error(self, message: str) -> None:
        self.decision_label.configure(text="ERROR", fg="#8a6d00")
        self.message_label.configure(text=message)
        self.detail_label.configure(text="")

    def _show_result(self, result: CheckResult) -> None:
        colour = {
            constants.DECISION_YES: "#1a7f37",
            constants.DECISION_NO: "#c02020",
        }.get(result.decision, "#8a6d00")
        self.decision_label.configure(text=result.decision, fg=colour)
        self.message_label.configure(text=result.message)

        ffr = result.ffr
        details = []
        if ffr is not None:
            details.append(
                f"FFR {ffr.table_name} ({ffr.source_label}) column {ffr.weight_column}"
                + (f", class {ffr.currency_class}" if ffr.currency_class else "")
                + f", weight {numbers.percent(ffr.weight)}"
            )
        details.append(
            f"notional {numbers.millions(result.request.notional_usd)}, "
            f"usage {numbers.millions(result.usage)}, "
            f"bucket {result.affected_bucket or '-'}"
        )
        if result.hold_id is not None:
            details.append(f"hold {result.hold_id} created (soft reservation, not a booking)")
        self.detail_label.configure(text="\n".join(details))
        self.sources_label.configure(text="sources: " + ", ".join(
            f"{table}={mode}" for table, mode in sorted(result.sources.items())))

        surface = result.surface
        if surface is None:
            self.deal_label.configure(text="deal limit: -")
            self._fill(self.bucket_table, [])
        else:
            self.deal_label.configure(text=(
                f"deal limit {numbers.millions(surface.deal_limit)}  "
                f"utilisation {numbers.millions(surface.utilisation)}  "
                f"holds {numbers.millions(surface.holds_usage)}  "
                f"available before {numbers.millions(result.deal_available_before)}  "
                f"this usage {numbers.millions(result.usage)}  "
                f"available after {numbers.millions(result.deal_available_after)}"
            ))
            self._fill(self.bucket_table, [
                (
                    bucket.bucket + (" *" if bucket.bucket == result.affected_bucket else ""),
                    numbers.millions(bucket.limit),
                    numbers.millions(bucket.occupied),
                    numbers.millions(bucket.holds_usage),
                    numbers.millions(bucket.available),
                )
                for bucket in surface.buckets
            ])

        self._fill(self.chain_table, [
            (
                node.counterparty + (" (submitted)" if node.is_submitted else ""),
                node.parent or "(none)",
                numbers.millions(node.surface.deal_limit) if node.surface else "-",
                numbers.millions(node.surface.utilisation) if node.surface else "-",
                numbers.millions(node.surface.holds_usage) if node.surface else "-",
                numbers.millions(node.surface.available) if node.surface else "-",
                node.agreement_text or "(no agreement text)",
            )
            for node in result.chain
        ])

    def refresh_peers(self, counterparty: str, product: str) -> None:
        now = datetime.now()
        try:
            peers: list[tuple[Hold, float]] = self.store.peers(counterparty, product, now)
        except StoreError as error:
            _logger.warning("holds unavailable: %s", error)
            peers = []
        self._fill(self.peer_table, [
            (
                hold.id,
                hold.username,
                hold.tenor,
                hold.affected_bucket,
                numbers.millions(hold.notional_usd),
                numbers.millions(hold.usage),
                f"{minutes:.0f}",
            )
            for hold, minutes in peers
        ])
        self._update_release_button()

    def refresh_history(self) -> None:
        try:
            records: list[CheckRecord] = self.store.history_today()
        except StoreError as error:
            _logger.warning("history unavailable: %s", error)
            records = []
        self._fill(self.history_table, [
            (
                f"{record.created_at:%H:%M:%S}",
                record.decision,
                record.username,
                record.counterparty,
                record.product,
                record.tenor,
                numbers.millions(record.usage),
            )
            for record in records
        ])

    def _update_release_button(self) -> None:
        """Release is enabled only on the logged-in user's own rows."""
        selection = self.peer_table.selection()
        if not selection or not self.logged_in_user:
            self.release_button.configure(state="disabled")
            return
        values = self.peer_table.item(selection[0], "values")
        owner = str(values[1]) if len(values) > 1 else ""
        own_row = owner.strip().lower() == self.logged_in_user.strip().lower()
        self.release_button.configure(state="normal" if own_row else "disabled")


def build_window(settings: Settings | None = None) -> tk.Tk:
    """Create the window without entering the event loop (used by the smoke test)."""
    settings = settings or load_settings()
    store = HoldsStore(settings)
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.minsize(1080, 900)
    LimitCheckApp(root, settings, store)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=WINDOW_TITLE)
    parser.add_argument("--config", help="path to config.ini")
    args = parser.parse_args(argv)
    setup_logging(logging.INFO, console_level=logging.WARNING)
    settings = load_settings(args.config)
    log_startup(settings)
    root = build_window(settings)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
