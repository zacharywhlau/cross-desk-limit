# Ten minute demo for the desk

Everything below runs on the **mock data** that ships with the repository: no endpoint,
no network, no desk data on screen. That makes it safe to show to anyone and repeatable
if a step goes wrong.

Run the whole sequence non-interactively first, so you know it works on the machine you
are about to demonstrate on:

```bat
scripts\demo.bat
```

```bash
scripts/demo.sh
```

The script prints each command before running it and stops at the first failure. It
finishes by telling you how to open the window.

---

## The cast in the mock data

- `ABCDEFG` - the main name, a subsidiary of `ABCDGRP`, comfortable headroom.
- `ABCDGRP` - the ultimate parent, much larger limits. Reference only.
- `ABCD` - a four character name with no parent.
- `EFGHIJK` - nearly exhausted, so a clean **N** is one command away.

All four products (FX, Gold, IRS, Equity swaps) have limits for every name. No mock
counterparty has an FX limit beyond five years, which is what makes the long-dated
rejection in step 4 realistic.

---

## The script, step by step

### 1 It answers Y, and says why (2 min)

```bash
python3 -m cdl.cli check --user edmund --cpty ABCDEFG --product FX \
    --tenor "1 months" --pair USDHKD --notional 500000
```

Point at three things in the output:

- `FFR`: `FFR_FX_LOW`, column `2025Q2`, weight `1.8%` - the weight came from the grid
  for HKD's volatility class, in the quarter the config says is in force.
- `usage 509,000` - that is `500,000 x (1 + 0.018)`, not the notional.
- `DECISION: Y` with the period `SPT-1M` and a hold id. The hold is a soft reservation,
  not a booking.

### 2 The ladder (3 min - this is the part worth explaining)

In the same output, the time period table. Say it in one sentence:

> A deal consumes its own period **and every shorter one**, so the number that decides
> is the running minimum down the ladder, not the period's own headroom.

Then show two rows: `3M-6M` has more room of its own than `1M-3M` allows, so it is
capped; and everything beyond five years is zero because this name has no long lines.

`docs/HOW_IT_WORKS.md` has the same table with the arithmetic spelled out if someone
wants it afterwards.

### 3 It answers N, and hard (1 min)

```bash
python3 -m cdl.cli check --user edmund --cpty EFGHIJK --product FX \
    --tenor 1M --pair USDHKD --notional 500000
```

50,000 available against 509,000 of usage: `N`, "Hard reject - no override and no
partial hold". No hold is written, but the N is in today's history.

### 4 The long end is closed (1 min)

```bash
python3 -m cdl.cli check --user edmund --cpty ABCDEFG --product FX \
    --tenor 10Y --pair USDHKD --notional 100000
```

`N` on period `7Y-10Y`, availability zero. This is the ladder doing its job on a name
with no long-dated limit, not a bug.

### 5 Two traders, one counterparty (2 min)

```bash
python3 -m cdl.cli check --user olivia --cpty ABCDEFG --product FX \
    --tenor 3M --pair EURUSD --notional 4000000
python3 -m cdl.cli peers --cpty ABCDEFG
```

Olivia's hold now appears in `peers` with the minutes remaining, and it has reduced what
edmund sees on the next check - that is the whole point of the tool. Holds expire
automatically after `hold_ttl_minutes` (60 by default) so a forgotten one cannot freeze
capacity.

### 6 Only the owner may release (30 sec)

```bash
python3 -m cdl.cli release --hold-id 1 --user olivia   # refused, hold 1 is edmund's
python3 -m cdl.cli release --hold-id 1 --user edmund   # released
```

### 7 History and the report (30 sec)

```bash
python3 -m cdl.cli history
```

Every Y, N and ERROR of the day, with the user who asked. `check` also writes
`report.html`, which is the same breakdown in a browser - useful for pasting into a
mail.

### 8 The window (1 min)

```bash
python3 -m cdl.ui.app
```

Seven sections top to bottom: login, the deal, the decision, the breakdown with the
ladder, the counterparty chain marked reference only, the traders who have asked with
Release enabled only on your own rows, and today's checks. Submit the same reference
deal to show it is the same engine as the command line.

---

## Questions you should expect

**"Where do the numbers come from?"** `TTCPIPP` for the counterparty and its parent,
`CKSBLMP` for the limits and the cash risk per period, `CKOVLMP` for the agreement
text, and the FFR grid for the weight. Each table can be switched between the real
endpoint, mock files and a local cache independently, and the check prints which source
it used for each.

**"Does it book anything?"** No. It reads the limit system and writes only to its own
SQLite file of holds and history.

**"What if the endpoint is down or slow?"** The answer is `ERROR` naming the table and
the mode. It never shows Y or N from partial data, and a result set that comes back at
the endpoint's row cap counts as partial.

**"Can I override an N?"** No. That is a deliberate rule, not a missing feature.

**"Are the weights up to date?"** They come from the quarter column named in
`config.ini`. If that column is missing the tool falls back to the newest quarter it can
find and logs which one it used, so a stale weight cannot pass unnoticed.

**"What is not confirmed yet?"** Section 9 of `docs/HOW_IT_WORKS.md` lists it, the
`CKBLOTP` layout being the important one.
