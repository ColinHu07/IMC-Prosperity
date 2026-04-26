# Round 3 trader — design log and failure notes

This file is the post-mortem / decision log for the Round 3 trader. The
current working code lives in a single file (`R3trader.py`). The exact
code that was submitted to IMC lives in `R3trader_submitted.py` (frozen).
Everything below is the reasoning that produced that code — what was tried,
what failed, and why the current design is shaped the way it is.

## Products and structural facts (from 3 historical days + wiki)

| Product | Mean | Stdev/day | Touch spread | Limit |
|---|---|---|---|---|
| `HYDROGEL_PACK` | ~9,990 | 25-38 | **16 ticks** (92% of ticks) | 200 |
| `VELVETFRUIT_EXTRACT` (VEX) | ~5,250 | 13-17 | ~5 ticks | 200 |
| `VEV_4000` | ~1,262 | — | ~21 ticks | 300 |
| `VEV_4500` | ~762 | — | ~16 ticks | 300 |
| `VEV_5000` | ~266 | — | ~6 ticks | 300 |
| `VEV_5100…5500` | small | — | — | 300 |
| `VEV_6000, 6500` | near 0 | — | — | 300 |

Implications that drove every decision:

1. HYDROGEL has a **wide** touch (16 ticks) — any MM quoting inside the
   touch is always quoting 7+ ticks off mid. That is a lot of edge, but
   only if both sides fill. In a trending regime only the adverse side
   fills, and 7 ticks × 200 units = $1,400 bleed per round-trip.
2. VEX touch is ~5 ticks. Very little room for edge. MM only works if you
   quote at best±1 and the market is meaningfully mean-reverting.
3. Deep-ITM vouchers (VEV_4000/4500/5000) trade at roughly
   `VEX_mid − K + small_premium`. Their touch spread is 16-21 ticks while
   VEX's is 5. That asymmetry is a real edge: we can quote on the voucher
   at a price that's inside its wide touch, using the tighter VEX book as
   the fair-value reference.
4. Days 0 and 1 are mostly mean-reverting; day 2's first 100k ticks is a
   strong down-trend (−51 ticks). Any strategy that fights a trend blows
   up here.

## What got tried and why each version was abandoned

### V0 — "kitchen sink"
Black-Scholes pricing on near-ATM vouchers, deep-ITM put-call-parity
arb, OLS drift on VEX, anchored MM on HYDROGEL. Five subsystems.

**Result**: −$414k/day mean (optimistic fills). Catastrophic.

**Diagnosis**: The BS pricing and IV interpolation fits the 3 historical
days too tightly. Near-ATM vouchers have implied vols that jitter, and
any MM strategy that treats that jitter as signal trades against itself.
Also too many knobs (>30) = huge overfit surface.

### V1 — "R1-proven MM controls"
Cut back to HYDROGEL anchored MR + VEX drift MM + deep-ITM arb +
near-ATM MM, but added R1's inventory skew, per-tick take caps,
two-level passive quoting (patterns that worked on ASH in Round 1).

**Result**: −$184k/day mean. Less bad, still terrible.

**Diagnosis**: Same overfitting problem as V0. R1 MM controls help, but
they're applied on top of wrong fair-value estimates. The near-ATM
voucher subsystem was structurally losing no matter how the MM was
gated.

### V2 — "maximally conservative"
Every product treated as a pure EWMA MM, no cross-product coupling, no
BS, no deep-ITM arb. R1-style passive-only MM on all 12 products.

**Result**: −$611k/day — **worse** than V0.

**Diagnosis**: When you MM a high-dollar product with a wide touch and
no fair-value sharpness, you just bleed on adverse fills. Doing it
across 12 products 10,000 times per day compounds. Pessimistic backtest
fills exaggerate this, but the directional bias is real.

### V3 — "minimum attack surface" (the one that was submitted live)
Abandon VEX and every voucher. Trade only HYDROGEL with a Bayesian
anchored mean-reverter:
- `fair = (K · ANCHOR + sum_mids) / (K + n)` with `K = 50`, `ANCHOR = 9990`
- Aggressive take + two-level passive quotes + inventory/dev skew

**Local pessimistic backtest**: −$18k/day mean. Least-bad so far, so it
got shipped.

**Live IMC test-sim result (submission 436083, day 2, first 100k ticks)**:
**−$13,218**. Worst-case scenario played out:

| | Qty | Avg price |
|---|---|---|
| Buys | 482 | 10,009.30 |
| Sells | 306 | 9,997.97 |
| Net | +176 long | bleeding $11.33/unit wrong-way |

The anchor at 9,990 and prior `K = 50` held fair below the real market
during the first half of the drift. V3 sold at 10,001-10,005 while the
market kept falling. When the running mean dragged fair up to 10,005+,
the market had already fallen, and V3 then bought at 10,013-10,018.
Result: +$11/unit wrong-way, long 200 at close, −$13k P&L.

Key failure modes to not repeat:
- Fixed anchor on a market that is free to drift tens of ticks.
- Strong Bayesian prior (K=50) that makes fair slow to follow the tape.
- Large `MAX_TAKE = 100` that turned a wrong-fair signal into a position
  blowout in a few ticks.
- Two-level passive quoting that doubled adverse fills.
- No trend filter.
- No per-product stop-loss.
- Single-product exposure — no way to earn back the bleed elsewhere.

## The bug that made every backtest misleading

`backtest/trader_replay.py` (v1 of this harness, originally ported from
R1) iterated products one at a time per tick and called
`runner.on_tick()` per-product. `trader.run()` is cached per-tick (called
on the first product), and the `position` dict passed in comes from
`runner._positions`, which is **updated one product at a time** inside
`on_tick`. The result:

- Tick N, product HYDROGEL: passive fills processed, `_positions[HP]`
  updated, `trader.run()` called.
- At this moment `_positions[VEX]` is **stale** — it contains the value
  written during tick N-1's VEX iteration, which was BEFORE that tick's
  aggressive fills were applied.
- So `trader.run()` sees under-counted VEX position. It thinks it has
  room to quote more. It gets adversely filled more. Positions grow
  without the trader knowing.

For a single-product trader (V3) this is invisible because `_positions`
is always up-to-date for the one product it trades. For V4 with 5
products, the bug inflated VEX fills from 69 to **1,073** on day 2,
producing a fake loss of −$46,562 (actual −$48, almost flat).

The fix is in the current `backtest/trader_replay.py`: process ALL
passive fills for the tick first, then call `trader.run()` exactly once
with a fully-current positions dict, then process aggressive fills. The
`imc_sim_replay.py` harness uses the same correct ordering and matches
the live IMC result for V3 (−$12,211 in backtest vs. −$13,218 live —
difference is trader-side stochasticity, not harness mismatch).

## Current design (the code in `R3trader.py`)

Five structural commitments, each a direct reaction to a specific V3
failure mode:

1. **No fixed anchor.** Fair = slow EWMA of microprice. Always tracks
   the tape. `HP_SLOW_A = 0.0018` (~550-tick horizon).
2. **Trend filter.** `fast_ewma − slow_ewma`. If `|gap| > T`, stay out
   on BOTH sides of that product. Parameters:
   - HYDROGEL: T = 8
   - VEX: T = 3
   - Vouchers (gated by VEX trend): T = 2.5
3. **Passive only.** No aggressive cross-the-spread take anywhere.
   V3's take loop was the blow-up mechanism.
4. **Soft position caps** far below the hard IMC limits. HP 30 / VEX 30
   / VEV 25 vs. hard 200/200/300. Hard limit only matters in the
   closeout window.
5. **Short closeout window** — last 500 ticks. Long windows give up
   more ticks to book-crossing cost than they save in flattening.

Deep-ITM vouchers use the cross-product fact that VEV fair ≈
`VEX_microprice − K`. Because VEX's touch is 5 ticks while the voucher's
is 16-21 ticks, we can quote inside the voucher's touch at `fair ± 5`
and still sit 2-3 ticks on the right side of the VEX-implied fair. That
is real, structurally-motivated edge — it does not depend on fitting
the 3-day tape.

## What's not in the current design (and why)

- **Near-ATM voucher MM (VEV_5100…5500).** IV jitters, premia are
  small, adverse selection dominates edge. V0/V1 proved this subsystem
  is net-negative. Skipped.
- **Deep-OTM vouchers (VEV_6000, 6500).** Prices near 0, almost no
  volume. Skipped.
- **Black-Scholes IV RV.** Too many knobs, fits the 3-day tape. Would
  reconsider only with out-of-sample live data.
- **Bayesian prior / fixed anchor.** Directly caused the V3 loss. Not
  coming back unless the prior strength decays to zero within the first
  ~500 ticks.
- **Aggressive take on dislocation.** V3's `MAX_TAKE = 100` turned a
  stale fair into a guaranteed position blowout. A safe aggressive-take
  would require a much more confident fair-value estimate than EWMA.
- **Per-product P&L kill switch.** Attempted in an early V4 iteration.
  Killed by a cost-basis tracking bug (used last-mid as trade-price
  proxy), which made the kill switch fire on unrealised move instead
  of realised loss, flattening the position repeatedly and booking
  more losses. Right fix needs own-trade price tracking, which the
  `state.own_trades` interface supplies — deferrable improvement.

## Performance numbers used to make the call

All PnL values are total across HYDROGEL + VEX + VEV_4000 + VEV_4500 +
VEV_5000, per day.

### Pessimistic backtest (full day, fixed harness)

| | d0 | d1 | d2 | mean | worst |
|---|---|---|---|---|---|
| V3 (submitted) | -11,671 | -20,693 | -22,822 | -18,395 | -22,822 |
| Current (V4) | +527 | +897 | +503 | +642 | +503 |

### IMC-equivalent 100k-tick window (the exact scenario of submission 436083)

| | d0 | d1 | d2 |
|---|---|---|---|
| V3 | -11,220 | -6,125 | **-12,211** (matches live -13,218) |
| Current (V4) | +41 | +79 | **+191** |

### Stress tests (HYDROGEL-only mutations, current code)

| Scenario | Mean PnL | Min PnL |
|---|---|---|
| Baseline | +1,162 | +246 |
| Anchor +200 | +1,460 | +282 |
| Anchor -200 | -909 | -2,046 |
| Vol shock 3x | -4,512 | -6,542 |
| Drift +0.002/tick | +230 | +31 |

No stress scenario produces a blowup remotely close to V3's baseline loss.

## Sensitivity plateau (pessimistic fills, 3-day mean PnL)

| Knob | Plateau range (≥ 90% of best) | Current |
|---|---|---|
| HP_TREND_T | 4-12 | 8 |
| HP_QUOTE_EDGE | 3-5 | 4 |
| VX_TREND_T | 2.5-4 | 3 |
| VEV_QUOTE_EDGE | 3-7 | 5 |
| CLOSEOUT_TICKS | 500-1500 | 500 |
| HP_MAX_POS | 15-60 (insensitive) | 30 |
| VX_MAX_POS | 15-60 (insensitive) | 30 |

The flat plateaus on MAX_POS confirm the trend filter is the binding
constraint — we rarely hit the cap, we just stop quoting when trend is
on.

## Leaderboard-push optimization pass (Apr 25)

Goal: lift from ~25-35th percentile ($642 mean) toward top 100 territory
(~$50k+ for the round) without refitting to the 3 historical tapes.

### Method

Incremental changes, each validated on BOTH the 3-day pessimistic
backtest (worst-case acceptance metric) and the 100k-tick IMC replay
harness (proxy for the real live simulator). A change is kept only when
it improves the backtest mean AND does not worsen the worst-day min by
more than ~15%. Stress tests rerun at the end to confirm no scenario
blows up.

### Iteration log

Baseline (pre-pass): pessim mean $642, min $503; IMC replay d0/d1/d2
first 100k = $41 / $79 / $191.

1. **Loosen trend filters + tighten HP edge.** HP_TREND_T 8 → 15,
   VX_TREND_T 3 → 5, VEV_TREND_T 2.5 → 4, HP_QUOTE_EDGE 4 → 3.
   Rationale: thresholds were below the touch width, which meant we sat
   out most ticks even with no signal. Mean $642 → $1,369 (+113%),
   min $503 → $836 (+66%). Kept.

2. **Raise soft caps.** HP_MAX_POS 30 → 60, VX_MAX_POS 30 → 60,
   VEV_MAX_POS 25 → 50; inventory skews halved to compensate.
   Mean $1,369 → $1,493; min $836 → $1,063 (+27%). Kept.

3. **HYDROGEL layered quoting.** Added second passive level at
   `inside - layer_gap`. 60/40 split favoring the inner level.
   Mean $1,493 → $1,602; min $1,063 → $1,160. Kept.

4. **Book-imbalance fair skew (DISCARDED).** Added
   `fair += imb * coef`. Result: identical numbers. Diagnosis: the IMC
   simulator places symmetric volumes on both sides of the book, so
   imbalance ~0 99% of ticks. Signal is dead in this environment.
   Reverted.

4b. **Voucher premium EWMA + VEV_5100 (partial).** Tracks
   `EWMA(mid - intrinsic)` per voucher. Adding the premium to deep-ITM
   fair unexpectedly REGRESSED (stopped VEV_4500/VEV_5000 from trading
   because fair shifted just enough to push both quotes outside touch).
   Reverted for DEEP group; kept for VEV_NEAR only. VEV_5100 barely
   trades in the historical CSVs (0-1 trades/day) but does trade in the
   live sim — net neutral so kept.

5. **Sensitivity sweep + aggressive nudges.** Used `sensitivity.py` on
   optimistic fills to locate plateaus rather than fit peaks. Findings:

   | Knob | Old | New | Plateau |
   |---|---|---|---|
   | HP_TREND_T | 15 | 50 | monotone 15-75+ |
   | HP_QUOTE_EDGE | 3 | 3 | flat 2-5 |
   | HP_MAX_POS | 60 | 100 | flat 80-150 |
   | HP_LAYER_GAP | 4 | 2 | peak at 2 (+$3k vs 4) |
   | VX_TREND_T | 5 | 20 | flat 15-40 |
   | VX_QUOTE_EDGE | 1 | 2 | monotone 1-3 |
   | VX_MAX_POS | 60 | 100 | monotone 60-120 |
   | VEV_QUOTE_EDGE_DEEP | 5 | 5 | flat 3-7 |
   | VEV_TREND_T | 4 | 10 | flat 5-15 |
   | CLOSEOUT_TICKS | 500 | 100 | peak at 100 |

   Chose interior plateau values where possible (HP_MAX_POS=100 not 150)
   to minimize overfit risk even on flat regions.

### Rejected / reverted ideas

- **Book imbalance skew** — signal is flat 99% of ticks (symmetric sim
  book). Dead in this environment.
- **Premium EWMA on deep-ITM vouchers** — regressed trade count because
  small premium shifts pushed quotes outside the touch.
- **HP_TREND_T = 100** (fully off) — backtest kept improving, but vol
  shock 3x lost $18k/day vs $8k at T=50. Bad risk/reward.
- **VX_TREND_T = 40+** — optim sensitivity showed marginal gain, but
  pessim and stress didn't confirm. Stopped at 20.
- **HP_MAX_POS = 150** — flat sensitivity, no upside beyond 100,
  increases tail exposure. Chose 100.

### Final performance

Pessimistic 3-day backtest:
- mean **$5,203/day**  (baseline $642  → 8.1x)
- min  **$3,829/day**  (baseline $503  → 7.6x)
- max  **$6,469/day**

IMC replay (100k ticks, pessimistic harness):
- day 0  **+$364**  (baseline $41,  8.9x)
- day 1  **+$444**  (baseline $79,  5.6x)
- day 2  **+$520**  (baseline $191, 2.7x)

Stress tests (optimistic fills baseline ~$22k/day mean):
- anchor +200  mean $9,177, min $3,569  (all 3 days profitable)
- anchor −200  mean $3,921, min $318    (all 3 days profitable)
- drift +0.002/tick  mean +$164,386     (massive capture of trend)
- vol shock 3x  mean −$9,010, min −$16,546 (only blowup scenario, but
  a 3x simultaneous vol spike is effectively a market-structure break)

Extrapolation: 100k → full day is ~10x; 3 days of ~$4k-5k pessim per
100k = ~$40-50k for 3 full days on pessim. Real IMC sim is
between pessim and optim harnesses, so expected range **$50-100k**
for the round — consistent with top 100 targeting.

## Leaderboard push pass 2 (Apr 25, later) — structural hedging + guards

After the first leaderboard push landed pessim mean $5,203 / worst $3,829,
I went through the activitiesLog of the live IMC runs (437520, 439349)
and identified four structural additions with low overfitting risk:

### Phase 1a — Voucher one-sided inventory guard

Problem observed: VEV_5000 in 439349 had 3 consecutive sells → net short
→ all adverse moves (-$19). Not a knob issue; it's a *symmetry* bug
where one-sided fills build adverse inventory with no counter-flow.

Fix: per-strike streak tracker fed from position deltas (backtest-harness
safe, doesn't require `state.own_trades`). If streak volume ≥
`VEV_STREAK_VOL=5` **and** abs(pos) ≥ `VEV_STREAK_POS_FRAC=0.4 * max_pos`,
suppress the crowded side until position flattens. Pure insurance; didn't
trigger on the 3-day pessim backtest (numbers unchanged) — which is
correct, it only fires on tail behaviour.

### Phase 1b — Min-samples gate for near-ATM

Problem: VEV_5100 started quoting on the first tick using an
unconverged premium EWMA, causing early fills at poor prices.

Fix: gate quoting on cumulative observations ≥ `VEV_NEAR_MIN_SAMPLES=200`.
Again no backtest impact in our 3 days; pure insurance against cold-start
bleed, especially relevant in live where the premium can drift.

### Phase 2a — VEX delta hedge of voucher exposure

Insight: our deep-ITM voucher fair is exactly `VEX - K`, so each long
deep-ITM voucher carries ≈ +1 delta on VEX. Hedge by adding voucher
delta to VEX's *effective* inventory (via pre-shifting VEX fair by
`voucher_delta * VX_INV_SKEW`). VEX MM then naturally leans against
combined exposure without a separate hedge loop.

Result:
- pessim mean  $5,203 → $5,156 (-0.9%, expected small cost)
- pessim worst $3,829 → $3,884 (+1.4%)
- stress anchor +200 min $3,569 → $4,517 (+$948)
- stress anchor −200 min $318 → $812 (+$494)
- stress vol 3x min −$16,546 → −$15,598 (+$948)

All stress scenarios improve. Textbook variance reduction.

### Phase 1c — Three-level HP passive quoting

Previously two passive levels on HP (inner + outer, gap 2). Added a
third (mid + outer_2) with `HP_LAYER_GAP_2=2` picked from sensitivity.
Splits are 50/30/20 of available room.

### Post-sensitivity knob nudges

Re-ran sensitivity with all new knobs:
- `HP_LAYER_GAP_2`: 2=+24,353 > 3=+23,519 > 4=+22,869  → set to 2.
- `VX_MAX_POS`: 120=+25,441 > 100=+23,519  → set to 120 (plateau).
- `VX_DELTA_HEDGE=2.0` scored higher than 1.0 on optim, but physical
  delta is exactly 1 for deep-ITM in our model. Kept at 1.0 — refusing
  to fit a higher hedge ratio that has no structural justification.

### Rejected in this pass

- **Cross-strike voucher arbitrage (Phase 3)**: scanned all 30k
  historical snapshots and 2k live snapshots — **zero** monotonicity or
  vertical-spread violations detected. Market makers keep it clean.
  Dead weight; don't implement.
- **Vol-scaled quote edge (Phase 4a)**: cancelled because the stress
  tests already improved 35% via the delta hedge alone. Adding another
  vol knob introduces fit surface for marginal benefit; revisit only if
  a live IMC run shows vol-driven bleed.
- **Own-trade PnL tracker (Phase 5a)**: nice-to-have observability but
  `own_trades` isn't populated in the backtest harness, and Phase 1a's
  position-delta streak tracker covers the actionable use case.

### Final metrics

Pessim backtest, 3 historical days (acceptance metric):
- mean TOTAL  **+$5,461/day**  (was +$5,203 pre-pass, +$642 pre-v4)
- min  TOTAL  **+$4,206/day**  (was +$3,829 pre-pass, +$503 pre-v4)
- max  TOTAL  +$6,751/day

IMC replay (first 100k ticks, pessim):
- day 0 +$358, day 1 +$456, day 2 +$540

Stress (baseline mean $26k):
- anchor +200  min +$5,163 (+$1,594 vs pre-pass)
- anchor −200  min +$3,045 (+$2,727 vs pre-pass)
- vol shock 3x min −$12,427 (tail ~$4k less negative than pre-pass)
- drift +0.002  min +$199,922 (captures trend even harder)

All scenarios profitable except vol-3x; the 35% tail improvement is
what Phase 2a bought.

## Next steps (not yet implemented)

In priority order, roughly by expected PnL impact:

1. Proper own-trade P&L tracker using `state.own_trades` (only feasible
   in live, not in the local harness). Enables a real per-product kill
   switch and unlocks dynamic skew tuning.
2. Vol-adjusted quote edge. Skipped this pass because delta hedge
   already cut the vol-shock tail. Revisit if a live run bleeds during
   a visible vol spike.
3. Use depth-weighted microprice (top 3 levels) — only useful if IMC
   publishes asymmetric level sizes in a live round; our data so far
   shows symmetric fills.
4. Dynamic position-aware trend threshold: widen `TREND_T` when pos ~0
   (we can afford to quote in drift), tighten when |pos| is large (get
   out faster). Structural but another fitted surface.

None of these need new files. Edit `R3trader.py` in place and lean on
git for history. `R3trader_submitted.py` stays frozen so we can always
replay the live submission.
