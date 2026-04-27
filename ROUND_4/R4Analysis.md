# Round 4 Analysis — Counterparty-Aware Strategy

This document is the in-depth design log for Round 4. It combines:

- empirical findings from the new counterparty-tagged data,
- direct comparison with Round 3 paths and the official `486657` outcome,
- a concrete strategy blueprint with parameters,
- a quantitative framework for the Aether Crystal manual challenge.

All numbers come from `prices_round_4_day_{1,2,3}.csv` and `trades_round_4_day_{1,2,3}.csv`, cross-referenced against the Round 3 files in `ROUND_3/` and the run artifact `ROUND_3/round3_results/486657/486657.json`.

---

## 0) Executive summary

1. **R4 prices are R3 prices.** Day 1 and 2 paths are identical to R3 day 1 and day 2. Day 3 is identical to the official contest path that produced `486657 = -$65,370`.
2. **The new alpha is purely the counterparty IDs.** The 7 named counterparties (`Mark 01/14/22/38/49/55/67`) split cleanly into two roles: passive market makers vs aggressive takers. PnL leaks deterministically from aggressors to makers via half-spread economics.
3. **Mark 14 is not an information signal — he is a competitor.** Mark 14’s P&L is half-spread × volume; his trades have ~50% directional hit-rate at any horizon. “Follow Mark 14” is wrong; "*be* Mark 14" is right.
4. **The 486657 disaster was a wrong-side aggressor mistake.** The friend strategy crossed the spread with full-size directional targets in the same products where Mark 38 was the loser-aggressor — but in the wrong direction. We paid the spread Mark 14 normally collects, on every leg, on the wrong side.
5. **Round 4 path-aware target:** capture the half-spread structurally available across HP, VEX, and VEV_4000 (~$15k/day fwd-PnL pool), without reintroducing directional voucher concentration. Range: `+$10k` to `+$45k` per 1M-tick day on this data, depending on aggressiveness of quote skew and tail caps.
6. **Manual challenge:** with σ = 251% annualized, options have huge time value (`50%` 2w, `61%` 3w). The chooser is a strong long structure (≈1.8× the max of vanilla call/put). Knock-out puts are nearly worthless above barrier 80. Binary put strikes near ATM are roughly fair around `0.5–0.6`.

---

## 1) Data equivalence proof

### 1.1 Path equality

`prices_round_4_day_3.csv` matches `486657.json` 100% on `VELVETFRUIT_EXTRACT` mid for all 10,000 timestamps. Day 1 and Day 2 match Round 3 day 1 / day 2 paths exactly.

| R4 file | Match | Notes |
|---|---|---|
| `prices_round_4_day_1.csv` | = R3 day 1 (`prices_round_3_day_1.csv`) | first/last/min/max all identical |
| `prices_round_4_day_2.csv` | = R3 day 2 (`prices_round_3_day_2.csv`) | identical |
| `prices_round_4_day_3.csv` | = R3 contest path (`486657.json`) | 10,000/10,000 VEX timestamps identical |

### 1.2 Implication

- All R3 backtests, ladders, and rust artifacts (`ROUND_3/runs/stab_check/...`) are still valid for R4 days 1 and 2.
- We finally have the contest path locally for repeatable testing (day 3 csv).
- Anything we discovered about path-dependence in R3 (max DD `-$67k` to `-$88k` on the high-conviction strategy) is directly carried over.

---

## 2) Counterparty roster and roles

### 2.1 Forward-PnL totals (3 days combined, 50-tick horizon)

| Mark | Volume | edge_now/qty | h=10 | h=50 | h=200 | h=1000 |
|---|---:|---:|---:|---:|---:|---:|
| Mark 14 | 8,718 | +5.7 | +49,074 | **+49,713** | +43,836 | +49,486 |
| Mark 01 | 7,428 | +1.4 | +10,736 | +10,278 | +9,286 | +8,840 |
| Mark 67 | 1,510 | -0.8 | +2,176 | +1,746 | +1,761 | -466 |
| Mark 49 | 1,186 | +0.7 | -1,696 | -1,190 | -1,829 | +280 |
| Mark 22 | 5,889 | -0.6 | -3,031 | -3,688 | -3,125 | -3,615 |
| Mark 55 | 6,551 | -2.5 | -15,866 | -13,204 | -9,052 | -15,258 |
| Mark 38 | 5,000 | -8.7 | -41,392 | **-43,656** | -40,877 | -39,266 |

The ranking is stable across **every horizon from 10 to 1000 ticks**. This is structural, not predictive. PnL is path-invariant in expectation (same per day too, see §2.4).

### 2.2 Passive vs aggressive classification (price relative to BBO)

For every trade I classified each side as **passive** (filled at the prevailing bid/ask), **aggressive** (crossed the spread), or **mid** (interior).

| Mark | Passive (resting) | Aggressive (crossing) | Role |
|---|---:|---:|---|
| Mark 14 | **8,703** | 0 | Pure passive market maker |
| Mark 01 | 5,206 | 0 | Pure passive accumulator (OTM voucher buyer) |
| Mark 49 | 1,169 | 17 | Mostly passive VEX seller |
| Mark 22 | 825 | 2,827 | Mixed; mostly aggressive seller of OTM vouchers |
| Mark 67 | 1 | 1,509 | Pure aggressive VEX buyer |
| Mark 38 | 0 | **5,000** | Pure aggressive (HP & VEV_4000 taker) |
| Mark 55 | 0 | **6,551** | Pure aggressive (VEX taker, both sides) |

The ranking is identical to forward PnL ranking: passive players win, aggressive players lose. Half-spread economics, period.

### 2.3 Edge per fill is exactly half the prevailing spread

For Mark 14 in HP and VEV_4000:

| Product | Side | n | avg spread | avg edge_vs_mid |
|---|---|---:|---:|---:|
| HP | buy | 496 | 15.96 | +7.98 |
| HP | sell | 507 | 15.88 | +7.94 |
| VEV_4000 | buy | 232 | 20.93 | +10.47 |
| VEV_4000 | sell | 207 | 20.75 | +10.37 |
| VEX | buy | 316 | 4.86 | +2.43 |
| VEX | sell | 331 | 4.92 | +2.46 |

`edge_vs_mid ≈ spread / 2` to within rounding. Mark 14 is *always* at the BBO. Mark 38 is *always* lifting the offer or hitting the bid, for the matching mirror loss.

### 2.4 Per-day stability

| Mark | Day 1 fwd-50 | Day 2 fwd-50 | Day 3 (contest) fwd-50 |
|---|---:|---:|---:|
| Mark 14 | +18,528 | +14,954 | +16,230 |
| Mark 01 | +3,634 | +3,160 | +3,484 |
| Mark 67 | +1,144 | +619 | -18 |
| Mark 49 | -735 | -440 | -15 |
| Mark 22 | -1,280 | -1,214 | -1,194 |
| Mark 55 | -4,633 | -4,292 | -4,280 |
| Mark 38 | -16,658 | -12,788 | -14,209 |

Mark 14’s alpha is `~+15k/day, ±2k`. Mark 38’s anti-alpha is `~−14k/day, ±2k`. The **counterparty environment is stationary** across all 3 paths, including the contest path. There is no “contest day was different” excuse. That spread economics existed; we missed it.

### 2.5 Volume by product per Mark (3-day totals)

| Product | Mark 14 | Mark 38 | Mark 01 | Mark 22 | Mark 55 | Mark 67 | Mark 49 |
|---|---:|---:|---:|---:|---:|---:|---:|
| HYDROGEL_PACK | 4,022 | 4,096 | 0 | 74 | 0 | 0 | 0 |
| VELVETFRUIT_EXTRACT | 3,524 | 0 | 2,792 | 843 | 6,551 | 1,510 | 1,186 |
| VEV_4000 | 870 | 876 | 0 | 6 | 0 | 0 | 0 |
| VEV_4500 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| VEV_5000 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| VEV_5100 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| VEV_5200 | 122 | 6 | 34 | 162 | 0 | 0 | 0 |
| VEV_5300 | 105 | 4 | 439 | 548 | 0 | 0 | 0 |
| VEV_5400 | 48 | 0 | 911 | 959 | 0 | 0 | 0 |
| VEV_5500 | 27 | 0 | 1,042 | 1,069 | 0 | 0 | 0 |
| VEV_6000 | 0 | 0 | 1,105 | 1,105 | 0 | 0 | 0 |
| VEV_6500 | 0 | 0 | 1,105 | 1,105 | 0 | 0 | 0 |

Critical structural observations:

- **VEV_4500/5000/5100 have basically no volume**. Only 6 lots traded in 3 days. These strikes were never a real market — taking aggressive directional positions there is illiquid by design.
- **HP and VEV_4000 are the Mark 14 vs Mark 38 ring.** All meaningful flow is between those two. If we are not Mark 14 in those products, we are leaving roughly `+$8` per HP fill and `+$10` per VEV_4000 fill on the table.
- **VEX is a four-player market** (Mark 14 / Mark 01 / Mark 55 / Mark 67/49). Mark 55 alone gives away `~$13k` in spread over 3 days. Capturing him is high-value.
- **OTM vouchers (≥5300)** are the Mark 01 vs Mark 22 ring. Mark 01 is a passive buyer paying `~+0.5` per fill. Mark 22 is an aggressive seller paying `~−0.5` per fill. Edge per contract is small but volume is high.
- **VEV_6000 / VEV_6500** trade only between Mark 01 and Mark 22, exactly `1,105 lots each` — these are essentially auto-quoted lottery markets with zero spread mispricing.

### 2.6 Time-of-day distribution (volume by 100k window, 3 days summed)

| ts window | M14 | M38 | M01 | M22 | M55 | M67 | M49 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0–100k | 766 | 425 | 852 | 711 | 673 | 135 | 96 |
| 100k–200k | 875 | 567 | 712 | 528 | 635 | 171 | 130 |
| 200k–300k | 924 | 535 | 610 | 469 | 709 | 180 | 151 |
| 300k–400k | 972 | 579 | 839 | 712 | 634 | 133 | 99 |
| 400k–500k | 831 | 439 | 710 | 480 | 684 | 109 | 103 |
| 500k–600k | 838 | 477 | 834 | 737 | 637 | 109 | 66 |
| 600k–700k | 821 | 490 | 735 | 605 | 547 | 136 | 82 |
| 700k–800k | 919 | 488 | 722 | 550 | 698 | 191 | 162 |
| 800k–900k | 884 | 457 | 696 | 549 | 688 | 159 | 163 |
| 900k–1000k | 888 | 543 | 718 | 548 | 646 | 187 | 134 |

Activity is essentially **uniform across the day**. There is no “first 100k is special” regime. Earlier R3 backtests showed the strategy went deeply negative in the first 100k for the friend code; that wasn’t volume — that was first-direction noise on top of full-size positions.

### 2.7 Important: directional predictive power is essentially zero

Hit-rate of Mark 14’s buy/sell direction on next 50 ticks of mid for VEV_4000 (day 3, contest path):

- Mark 14 BUYS: `48%` up, avg fwd move `+0.04`
- Mark 14 SELLS: `49%` down, avg fwd move `+0.13`

Same picture for Mark 38 and HP. **There is no directional signal here.** “Follow Mark 14” would be an overfit. Mark 14 is rich because he is at the BBO with priority, not because he predicts the next move.

This kills the natural mistake of treating Mark 14 as a leading indicator and stamping in directional voucher trades behind him. That is exactly the failure mode that produced `486657`.

---

## 3) Why `486657` lost `-$65,370` — counterparty-aware retelling

### 3.1 Loss decomposition

| Product | PnL |
|---|---:|
| HYDROGEL_PACK | +7,176 |
| VELVETFRUIT_EXTRACT | -18 |
| VEV_4000 | **-37,742** |
| VEV_4500 | **-23,751** |
| VEV_5000 | -3,413 |
| VEV_5100 | -1,068 |
| VEV_5200 | -521 |
| VEV_5300 | -1,591 |
| VEV_5400 | -2,362 |
| VEV_5500 | -2,080 |
| VEV_6000 | 0 |
| VEV_6500 | 0 |

VEV_4500 had only `~6 lots` traded by all real counterparties in 3 days. Yet our strategy lost `-$23,751` there. That means we were trading against ourselves into our own quote stack, paying the entire bid-ask spread every time we crossed.

### 3.2 The mechanism

The friend code (`479868.py`) crosses to a directional target whenever the EMA-dislocation signal flips. On the contest path:

1. The signal stamped in **full positions** (`±300 × 6 strikes`).
2. In VEV_4000, the only real counterparty pair is Mark 14 (passive, ask side `+10`) vs Mark 38 (aggressive). When we cross, we pay `+10` to Mark 14, not collect it.
3. In VEV_4500/5000/5100, there are **no real counterparties**. The fills are mostly mechanical against thin synthetic depth, every cross pays the full spread.
4. As the underlying drifted further away, expansion-stress logic kept adding to the wrong side.

So `-$65k` is essentially `(half_spread × 2) × volume_we_aggressed × wrong_direction`. Counterparty data confirms we acted as another Mark 38 — the loser-aggressor — across deep ITM strikes.

### 3.3 What would have prevented it given the counterparty data

- Prohibit aggressive crossing in any product where Mark 14 is sitting at the BBO. Specifically HP and VEV_4000.
- Cap directional voucher exposure aggressively for strikes with `<50 lots/day` real volume (4500, 5000, 5100). These are not tradable in size; they are pricing artefacts.
- Switch to passive quoting one tick inside Mark 14 instead of crossing.

We can verify these counterfactuals once the rust backtester loads the R4 day 3 csv (which is the contest path).

---

## 4) Round 4 strategy blueprint

### 4.1 Architecture

```
Trader.run(state):
    update_microprice_book(state)
    update_counterparty_metrics(state)        # NEW: per-Mark rolling stats
    spread_capture_orders   = make_passive_quotes(state)  # base layer
    aggressor_skew_orders   = adjust_skew_with_counterparty(state)
    micro_alpha_orders      = mr_or_ema_layer(state, capped=True, gated=True)
    return enforce_position_caps_and_dd_gates(orders)
```

### 4.2 Counterparty signal layer (cheap, deterministic)

Maintain rolling-window stats per `(mark, product)`:

- `signed_qty_recent[m][s]` (decay over ~5k ticks)
- `agg_count[m][s]`
- `pass_count[m][s]`
- `last_seen_ts[m][s]`

These are computed from `state.market_trades` each tick. The decision logic uses them to:

1. **Tighten quotes when an aggressor is active** in our product. If recent net signed flow from aggressors (Mark 38, Mark 55) is heavily one-sided, post inside their crossing direction by 1 tick. Capture their spread.
2. **Skip aggressive crossing if Mark 14 is on the same side as our intended aggression.** That means we’d be paying Mark 14’s spread and competing with him — bad trade.
3. **Optional micro-alpha:** a small mean-reversion layer on top, but with caps that scale **down** when spread is wide and aggressors are active (high adverse selection regime).

### 4.3 Concrete parameters (proposed starting points)

```python
HP_BASE_QUOTE_QTY      = 18
HP_QUOTE_INSIDE_M14    = 1     # post 1 tick inside Mark 14's last-seen quote
HP_TAKE_EDGE           = 6     # only cross if mid edge > 6
HP_CAP                 = 200

VEX_BASE_QUOTE_QTY     = 18
VEX_QUOTE_INSIDE_M14   = 1
VEX_TAKE_EDGE          = 3
VEX_CAP                = 200

VEV4000_QUOTE_QTY      = 5
VEV4000_TAKE_EDGE      = 12    # very high; almost never cross
VEV4000_CAP            = 100   # cut from 300

VEV4500_5100_TRADE     = False  # illiquid; do not directional-trade

VEV5200_5500_QUOTE     = True   # passive only, Mark 01-side
VEV5200_5500_QTY       = 4
VEV5200_5500_CAP       = 60

VEV6000_6500_TRADE     = False  # auto-quoted lottery, no edge

DD_KILL_THRESHOLD      = -25_000   # if cum < this, flatten directional overlay
GROSS_VOUCHER_CAP      = 600       # sum |pos| across all VEV
```

These are designed so:

- HP/VEX maker layer captures most of Mark 38/Mark 55 spread.
- VEV_4000 is touched but never aggressed.
- VEV_4500/5000/5100/6000/6500 are explicitly dead products for our directional engine.
- VEV_5200–5500 we accumulate **slowly** behind Mark 22’s aggressive sell flow, capped well below position limits.

### 4.4 Hard risk gates

These are non-negotiable pre-submit checks:

1. `min(cum_pnl) ≥ DD_KILL_THRESHOLD * 1.5` over 3-day rust runs. If breached on any day, fail.
2. `max_drawdown < 0.5 × mean_final_pnl`. Mirrors what we missed in R3.
3. No single product contributes `< -10k` final loss on any day. Flag if it does.
4. Sum of voucher gross exposure never exceeds `GROSS_VOUCHER_CAP` for >1 tick.
5. On R4 day 3 (contest path), final ≥ `−$5k` and max DD ≥ `−$15k`. This is the explicit "do not repeat 486657" gate.

### 4.5 Expected PnL band

Counterparty alpha pool per day (3-day mean, fwd-PnL of all aggressors): `+$60k–$70k`.

Realistic capture rates depending on quote aggressiveness:

| Strategy | Expected daily | 1M-tick range |
|---|---:|---:|
| Conservative MM (existing v4 baseline, no Mark logic) | +$25k | +$18k…+$45k |
| v4 baseline + Mark-aware quote skew | **+$30k–$40k** | +$25k…+$55k |
| Aggressive maker + thin caps | up to +$50k | wide; risk of `-$10k` tail days |
| Friend `479868` unchanged | unbounded variance | –$65k…+$250k |

The recommended target for R4 is the second row: structural improvement on top of v4 with deterministic risk-bounded counterparty layer. Aim for `+$80k` 3-day total with negative-day floor `≥ −$15k`.

---

## 5) Validation plan (rust + python)

1. Re-run `test_deep_tuned_v4_hp_anchor.py` (current safe baseline) and the new `Mark-aware` variant on `ROUND_4/prices_round_4_day_{1,2,3}.csv`.
2. Capture for each: `final`, `min_cum`, `max_drawdown`, `per_product`, `mark_capture_rate` (fraction of fills that were against Mark 38/55/22).
3. Pessimistic Python replay on day 3 (contest path) as a stress check.
4. Acceptance:
   - Day 1, Day 2: final ≥ baseline by `+$5k+`.
   - Day 3: final ≥ `−$5k`, max_dd ≥ `−$15k`.
   - All days: gross voucher exposure never breached.

---

## 6) Manual challenge — Aether Crystal & exotics

### 6.1 Inputs

- Underlying: `AETHER_CRYSTAL`, GBM, **0 risk-neutral drift**, σ = **2.51 (251% annualized)**.
- 4 steps per trading day, 252 trading days/year ⇒ `dt = 1/(252·4) ≈ 9.92e-4`.
- Vanilla 2-week (10 trading days, 40 steps): per-period vol `≈ 50.0%`.
- Vanilla 3-week (15 trading days, 60 steps): per-period vol `≈ 61.2%`.

### 6.2 Vanilla price reference table (Monte-Carlo, 20k paths, S0 = 100)

| Strike (% of S) | 2w call | 2w put | 3w call | 3w put |
|---|---:|---:|---:|---:|
| 80 | 29.72 | 9.45 | 33.20 | 13.34 |
| 100 | 20.07 | 19.80 | 24.34 | 24.48 |
| 120 | 13.46 | 33.19 | 17.92 | 38.06 |

Implications:

- ATM 3w call/put is ~24% of underlying — extreme time value.
- Even **20% OTM** options are worth ~13–18% of the underlying.
- This means anything quoted near intrinsic value is wildly cheap, and anything near "double the intrinsic" is roughly fair.

### 6.3 Chooser option

- Definition: at 2-week mark, holder picks call or put for the remaining 1-week period.
- Monte-Carlo (S0=100, K=100): `Chooser ≈ 44.18`, against `max(3w call, 3w put) = 24.48`.
- Premium over the better single-side: `+19.7` ≈ `1.81×`.

If quoted below `~36`, **buy aggressively**. The chooser is structurally underpriced unless market quotes match the sum-style structure.

### 6.4 Binary put

Pays a fixed amount if `S_T < K`. With 0 drift and 61% vol over 3 weeks:

| Strike | Prob `S_T < K` (binary value per $1 payoff) |
|---|---:|
| 80 | ~0.43 |
| 100 | **~0.62** |
| 120 | ~0.74 |

ATM binary put ≈ 0.62 per unit payoff. **Buy below 0.55, sell above 0.70.**

### 6.5 Knock-out put (3w, K=100)

Down-and-out put: if underlying ever trades below barrier `B` before expiry, option is worthless.

| Barrier B | KO put value | Vanilla put | Knock-out probability |
|---|---:|---:|---:|
| 60 | 3.05 | 24.48 | 47.9% |
| 70 | 1.03 | 24.48 | 61.7% |
| 80 | 0.24 | 24.48 | 74.5% |
| 90 | 0.02 | 24.48 | 86.1% |

These are nearly worthless except for the deepest barrier. **Strategy:**

- If a KO put with B=60 is quoted < `2.0`, it’s cheap — buy.
- KO puts with B≥80 are essentially worthless; if quoted at any meaningful price, **sell aggressively**.

### 6.6 Manual challenge cheat-sheet

1. **Buy chooser** if quoted below `~36` per unit (S0=100).
2. **Sell ATM vanilla call+put combo** at any quote ≥ `50` (sum) — extreme cherry pick if market is light.
3. **Buy ATM binary put** below `0.55`, **sell** above `0.70`.
4. **Sell shallow-barrier KO puts** (B≥80) at any positive quote.
5. **Buy deep-barrier KO puts** (B≤60) below `~2`.
6. **Avoid unhedged short calls/puts** — variance over 3 weeks is ~60%, single moves can be 2–3 σ catastrophic.

Sizing: respect the displayed volume cap. Treat each trade as a binary decision: in-or-out at the quoted price. Diversify across 4–5 mispricings, never concentrate >40% of risk in one structure.

### 6.7 Risk note

- All values above assume `S0 = 100` for ratio purposes; true monetary values will scale linearly with the actual quoted underlying.
- Rebuild this table once portal opens with the actual `AETHER_CRYSTAL` mid and the actual quoted strikes/barriers/payoffs. The script in `ROUND_4/manual_pricing.py` (to be created) will print the same table parametrized.

---

## 7) Round 4 “do this / don’t do this” checklist

Do:

- Add a `state.market_trades`-driven `update_counterparty_metrics()` step.
- Quote inside Mark 14’s level by 1 tick when our inventory permits.
- Capture Mark 38 and Mark 55 flow with passive sizing.
- Cap voucher gross exposure and kill-switch on intraday DD.
- Pre-trade rust-validate on day 3 csv (the former contest path).

Don’t:

- Don’t cross spreads aggressively in HP / VEV_4000 just because of a model signal. Mark 14 will collect the half-spread you pay.
- Don’t enter directional positions in VEV_4500 / 5000 / 5100. They are illiquid; you are trading against synthetic depth.
- Don’t treat Mark 14 as a directional indicator. He has no edge in direction; only in queue.
- Don’t replay the friend `479868` strategy at full size. Same code already produced `-$65k` on this exact path. The data has not changed; only the labels.

---

## 8) Open work items

- [ ] Implement `update_counterparty_metrics()` in trader.
- [ ] Build `ROUND_4/manual_pricing.py` script for live exotic pricing.
- [ ] Rust-test v4-baseline on R4 day 1/2/3 to confirm carry-over.
- [ ] Rust-test "Mark-aware v4" variant on same days; compare deltas.
- [ ] Update `ANALYSIS.md` (Round 3) with a back-reference to this file.
- [ ] Pre-submit checklist: confirm DD, gross caps, day 3 floor, day 1/2 ≥ baseline.

---

## 9) Bottom line

Round 4 is a labels round, not a price round. The price paths are already known and already partially solved. The real work is implementing a clean counterparty-aware execution layer and refusing to repeat the directional-voucher mistake from `486657`. The math says there is `~$60k–$70k` of half-spread alpha sitting on the table per day across all 7 Marks; capturing `40–60%` of it without taking tail risk is the achievable, robust outcome.
