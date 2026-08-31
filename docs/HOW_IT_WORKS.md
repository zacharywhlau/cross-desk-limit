# How the decision is made

This is the document to read before demonstrating the tool, and the one to hand a desk
user who asks "why did it say N". Every figure in the examples comes from the mock data
in `data/mock_treats/`, so anyone can reproduce them off the corporate network.

Contents:

1. What the tool answers
2. Step 1: validate the input
3. Step 2: the counterparty and its parent chain
4. Step 3: the FFR weight
5. Step 4: usage
6. Step 5: availability, the part most people get wrong
7. Step 6: the decision
8. Temporary holds
9. What is still provisional

---

## 1 What the tool answers

For one proposed deal: **does the counterparty still have the capacity to take it?**

The tool reads the limit system, it never writes to it. A **Y** does not book anything;
it records a **temporary hold**, which is a soft reservation so that a teammate looking
at the same residual capacity ten minutes later sees the claim. Booking happens
elsewhere, by other teams.

Six numbered steps, in this order, both in the standalone
`prototype/check_limit.py` trace and in the package:

```
[1] validate the input          [4] usage = notional x (1 + weight)
[2] counterparty + parent chain [5] availability ladder
[3] limits and cash risk        [6] Y / N / ERROR
```

---

## 2 Step 1: validate the input

Checked before any query is sent, so a typo never costs a round trip:

- `counterparty`: uppercase alphanumeric, length exactly 4 or exactly 7.
- `product`: FX, Gold, IRS or Equity swaps.
- `tenor`: one of the 89 values of the FFR "Time Period" grid, or an alias
  (`spot`, `1W`, `1M`, `6M`, `1Y`, `10Y`, `30Y`, ...). An unknown tenor produces a
  message that lists valid examples.
- `pair_or_currency`: 3 letters (`HKD`) or 6 (`USDHKD`).
- `notional_usd`: a positive number.
- `direction`: buy or sell. Stored, shown, and **not used in the formula** - there is
  no netting yet.
- `username`: whatever was typed at login. No password, no directory lookup.

---

## 3 Step 2: the counterparty and its parent chain

`TTCPIPP` holds the counterparty master: `XJCPAC` is the acronym, `XJPRAC` the parent.
Following `XJPRAC` repeatedly gives the ownership chain up to the ultimate parent, for
example `ABCDEFG > ABCDGRP`.

**The decision uses the submitted counterparty only.** Parent and ultimate-parent
figures are displayed as reference and never change a Y into an N or the other way
round. The chain is shown because a trader often wants to know that the name in front
of them is a subsidiary of a group that has plenty of room, or none.

`CKOVLMP.CIRFMG` is the agreement text, displayed as it comes.

---

## 4 Step 3: the FFR weight

The FFR weight is the risk factor that turns notional into limit consumption. It rises
with maturity, and for FX also with how volatile the currency is: a one month major
pair deal eats far less headroom than a ten year exotic of the same size.

Resolution, in order:

1. **Classifying currency.** A 3-letter input is the currency. For a 6-letter pair the
   non-USD side is taken (`USDHKD` -> `HKD`, `EURUSD` -> `EUR`); if neither side is USD
   the quote currency is used. PROVISIONAL.
2. **Currency to class.** `Low`, `Normal`, `Medium` or `High`, from a list in
   `src/cdl/constants.py`. An unlisted currency is treated as `High`, which is the
   conservative choice. PROVISIONAL.
3. **Class to rows.** One function, `resolve_ffr_selection`, decides where to read:
   - `ffr.source = mock`: one file per class, `FFR_FX_LOW.csv` and friends.
   - `ffr.source = api`: the configured table (`CKBLOTP`) through the same connector,
     payload and SQL builder as every other table.
   - `ffr.source = excel`: one workbook, one sheet per class. The fallback we do not
     want, kept only in case the weights turn out not to be queryable.
4. **Which quarter.** The grid is a matrix: each row is a maturity in a column called
   `Time Period`, each further column is a published quarterly snapshot (`2025Q1`,
   `2025Q2`, ...). Config `ffr.weight_column` says which one is in force, so a new
   quarter is a config change and not a code change. If that column is missing, the
   highest-sorting `20\d\dQ[1-4]` column is used **and the substitution is logged**, so
   a silently stale weight is impossible.

Cell values may read `1%`, `2.5%`, `0.01` or `1`; all four parse to a fraction.

Worked example (mock data): FX, `USDHKD`, `1 months`.
`USDHKD` -> `HKD` -> class `Low` -> `FFR_FX_LOW.csv`, row `1 months`, column `2025Q2`
-> `1.8%` -> **weight 0.018**.

---

## 5 Step 4: usage

```
usage = notional_usd x (1 + ffr_weight)
```

Each product has its own function (`fx_usage`, `gold_usage`, `irs_usage`,
`equity_swap_usage`) plus a registry, so one product's formula can change later without
touching the others. All four currently implement the shared default above.

Worked example: `500,000 x (1 + 0.018)` = **509,000**.

---

## 6 Step 5: availability, the part most people get wrong

`CKSBLMP` holds one row per counterparty and limit type (`CFSLMT`, e.g. `FX 01`):

- `CFSLTT` - the total approved limit.
- `CFSL01 .. CFSL14` - the approved limit of each of the 14 time periods.
- `CFSO01 .. CFSO14` - the cash risk already outstanding in each period.

The 14 periods, shortest first:

```
 1 CALL     2 TDY      3 TOM      4 SPT      5 SPT-1M
 6 1M-3M    7 3M-6M    8 6M-1Y    9 1Y-3Y   10 3Y-5Y
11 5Y-7Y   12 7Y-10Y  13 10Y-15Y 14 15Y+
```

**The limits are cumulative, not independent.** A deal in one period consumes headroom
in that period *and in every shorter one*. So availability is a ladder, computed
shortest first:

```
reverse_cum[i] = sum(cash risk of period j for j >= i)      # this tool's holds included
available[i]   = max(0, min(limit[i] - reverse_cum[i], available[i - 1]))
```

Two consequences worth saying out loud when demonstrating:

- Risk booked far out reduces the short end, because it is inside every
  `reverse_cum` below it.
- A short period that is fully used blocks every longer period, because the running
  minimum carries forward. A ten year deal cannot be done if the spot period is full.

Worked example - mock `ABCDEFG`, product FX. Total limit 20.00mm, cash risk 3.50mm
spread over the short periods. Figures in millions:

```
period    limit   cash risk   risk >= here   own headroom   available
CALL      20.00      0.00         3.50           16.50        16.50
SPT       20.00      0.10         3.50           16.50        16.50
SPT-1M    20.00      1.10         3.40           16.60        16.50   <- 1 months lands here
1M-3M     12.00      0.80         2.30            9.70         9.70
3M-6M     12.00      0.60         1.50           10.50         9.70
6M-1Y     12.00      0.50         0.90           11.10         9.70
1Y-3Y      6.00      0.40         0.40            5.60         5.60
3Y-5Y      6.00      0.00         0.00            6.00         5.60
5Y-7Y      0.00      0.00         0.00            0.00         0.00
15Y+       0.00      0.00         0.00            0.00         0.00
```

Read the last two columns together: `SPT-1M` has 16.60mm of its own room but only
16.50mm is available, because `CALL` also carries the spot risk and is tighter.
`3M-6M` has 10.50mm of its own room but is capped at 9.70mm by `1M-3M`. Nothing at all
is available beyond five years, because this counterparty has no limit there - exactly
what the desk screen shows for a name with no long-dated lines.

The tool's own **active holds** are added to the cash risk of their period before the
ladder is computed, which is how a teammate's claim reduces what you can do.

---

## 7 Step 6: the decision

**Y** only if the usage fits **both**:

- the affected period's ladder availability (which already covers every shorter
  period), and
- the total limit.

Otherwise **N**. Insufficient limit is a hard reject: no override, no partial hold.

If any required source fails - a table, the FFR grid, a truncated result set - the
answer is **ERROR** naming the table and the source mode. There is never a Y or an N
from partial or stale data. That is also why a read that comes back with exactly
`[treats] max_rows` rows is refused: the endpoint caps a result set, and a capped read
looks exactly like a complete one.

Worked example: usage 509,000 against `SPT-1M` availability 16,500,000 -> **Y**, and a
hold for 509,000 is written. The same deal on mock `EFGHIJK`, whose short end is nearly
exhausted, has 50,000 available -> **N**.

---

## 8 Temporary holds

- Created on Y only. Both outcomes are written to history.
- They expire after `hold_ttl_minutes` (default 60), so a forgotten hold cannot freeze
  capacity.
- Only the username that created a hold may release it.
- Expiry and release free capacity immediately.
- Holds stack: two traders can both hold on the same counterparty, and the second sees
  the first's claim.

One decision is one SQLite transaction opened with `BEGIN IMMEDIATE`: expire stale
holds, re-read the active ones, compute availability, insert history, insert the hold on
Y, commit. That is what stops two traders spending the same last capacity at the same
moment. On a shared network path the database uses the rollback journal rather than WAL,
which needs shared memory and is unsafe over SMB.

---

## 9 What is still provisional

Marked `PROVISIONAL` in the code, each in one place to edit:

- The `CKBLOTP` column layout and how a product or currency class selects the right
  rows - `resolve_ffr_selection` in `src/cdl/logic/ffr.py`. Top priority.
- The limit type codes for Gold, IRS and Equity swaps; only `FX 01` is confirmed -
  `constants.LIMIT_TYPE_BY_PRODUCT`.
- The period boundaries that map a tenor onto one of the 14 slots -
  `bucket_for` in `src/cdl/logic/tenor.py`. `CALL`, `TDY` and `TOM` are read and
  displayed but no tenor maps onto them; confirm whether a trader needs to submit them.
- The official currency to class lists - `constants.CURRENCY_CLASS_BY_CURRENCY`.
- The classifying currency rule for a pair with no USD leg -
  `classifying_currency` in `src/cdl/logic/ffr.py`.
- Whether the reserved / unreserved split on the desk screen needs to be shown; the
  tool currently works with the unreserved figures only.
- Local versus global limit: the tool uses the per-period (local) limits.
- `CIRFMG` beyond displaying the text.
