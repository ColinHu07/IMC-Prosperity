# Round 4 Methods - Bot Handoff

This file is the single source of truth for what to do next.

## Goal
- Maximize portal PnL for Round 4 while keeping runtime/validator-safe behavior.
- Prefer robust, repeatable improvements over overfit complexity.

## Current State
- Current best portal run: `496409 = +909.959`.
- Current candidate branch: **Mark-led overhaul** (this file's `R4trader.py`).
- Frozen fallback file: `R4 results/496409/496409.py`.

Other top references (older):
- `494694 = +851.600`
- `495103 = +851.600`
- `496203 = +851.529`
- `496831 = +909.959` (tie, micro VEV_L2 tweak)

## Mark-Led Overhaul (current `R4trader.py`)

### Why this exists
Tweaking parameters around the +909.96 plateau (size bumps, `inv_skew`,
`VEV_L2_RATIO`) had stopped producing portal-PnL gains. The user
explicitly asked for a structural method change driven by what the
Mark counterparties were doing, not more numerical tuning.

### Architecture
Per tick:
1. `_record_mark` (unchanged): EWMA fast/slow microprice per symbol.
2. `_update_mark_telemetry` (new): per-symbol decayed scores for
   - `agg_buy[s]` (Mark 38/55/22 lifting offers)
   - `agg_sell[s]` (Mark 38/55/22 hitting bids)
   - `maker[s]` (Mark 14/01 presence)
   - Time-decay (8000-step for aggressors, 15000-step for makers)
   - Incremental dedupe via `(last_trade_ts, last_trade_sig)` and
     `if tts == prev_ts and sig <= prev_sig: continue`
   - Hard age cutoff: 30000 timestamps.
3. `_classify_regime` (new): returns one of
   `MAKER_DOMINANT / BUY_AGGRESSOR / SELL_AGGRESSOR / QUIET`
   based on telemetry thresholds (`AGG_THRESHOLD = 1.5`,
   `MAKER_THRESHOLD = 1.0`, 1.25x dominance margin).
4. `_inventory_state` (new): `NEUTRAL` (<= 0.40 of cap),
   `STRETCHED` (0.40 - 0.75), `EXTREME` (> 0.75).
5. `_policy_for` (new): per-product policy table.
   - HP: two-level ladder always-on, `inside_sell=1` on BUY_AGG,
     `inside_buy=1` on SELL_AGG, size_scale 1.05 on MAKER, 0.90 on QUIET.
   - VEX: same shape but L2 narrower, `skew_scale=1.15`, size 1.0/0.85.
   - VEV_4000: L1 only; in BUY/SELL_AGG, quote opposite side only;
     in MAKER/QUIET, two-sided.
   - Inventory overlay applied after regime decision: STRETCHED kills
     risk-increasing L2; EXTREME drops to flatten-side L1 only.
6. `_emit_orders` (new): policy-driven order emission. No hidden gating,
   no event windows. Sides come from the policy.
7. Voucher layer (unchanged in spirit):
   - `VEV_5200/5300/5400/5500`: passive baseline, no mark logic.
   - `VEV_4500/5000/5100/6000/6500`: flatten-only.
   - `GROSS_VOUCHER_CAP` and `DD_KILL_SWITCH` unchanged.

### Constants kept
- `inv_skew = 0.035 * skew_scale * pos`
- Integer prices and quantities everywhere.
- `traderData` size bound + decay-based pruning + age cutoff.

## Validation Results

### Local replay (ROUND_3 days 0/1/2)
Sanity check only — R3 trades have no buyer/seller tags so the regime
engine sees QUIET on every product, which deliberately throttles
HP/VEX size by ~10%. This is expected and intended.

| Day | mark-led | 496409  | Δ |
|-----|----------|---------|---|
| 0   | 20,022   | 21,812  | -1,790 |
| 1   | 30,347   | 32,061  | -1,714 |
| 2   | 13,744   | 15,536  | -1,792 |
| min | 13,744   | 15,536  | -1,792 |

Acceptance: no collapse, all days strongly positive. PASS.

### Rust backtester (ROUND_4 days 1/2/3)
Closer to portal truth. R4 has Mark counterparty tags so the regime
engine actually fires.

| Day | mark-led | 496409  | Δ |
|-----|----------|---------|---|
| 1   | 21,761.50 | 21,839.00 | **-77.5** |
| 2   | 11,518.50 | 11,230.00 | **+288.5** |
| 3   | 17,870.50 | 17,949.50 | **-79.0** |
| Sum | 51,150.50 | 51,018.50 | **+132.0** |

Per-product on R4 (mark-led vs 496409):
- `HYDROGEL_PACK`: identical on all 3 days (regime mostly classifies
  MAKER_DOMINANT, size_scale 1.05 ≈ frozen behavior).
- `VELVETFRUIT_EXTRACT`: -77.5 / +288.5 / -79.0. Day 2 is the carry.
- `VEV_4000`: identical (regime stayed MAKER/QUIET → two-sided as
  before; no aggressor regime triggered the one-sided skip).
- `VEV_5200..5500`: identical.

Acceptance gate from plan: "must beat current best `+909.96` on portal;
if rust shows clear regression on day 3, do not submit."
- Day 3 delta is -79 / 17949 ≈ -0.44%: NOT a clear regression.
- Aggregate is +132: marginal positive.
- Verdict: cleared for portal submission.

## Decision Rule for Next Portal Attempt
- Submit current `R4trader.py` once.
- If portal beats `+909.959`: keep this branch as the new baseline,
  then iterate inside this architecture (regime thresholds, inside-tick
  parametrization, VEV_4000 thresholds).
- If portal ties or regresses: revert to `496409` behavior. The rust
  edge is small and noise-dominated; do not push further variants of
  this architecture without first changing the Mark telemetry inputs.

## Key Lessons (Do Not Re-Learn)
1. **Hard/soft participation suppression hurts.** Gated/event-window
   methods underperformed (`494795`, `495565`, `496047`). The new
   architecture has zero hidden gating: only explicit side selection
   from the policy table.
2. **Continuous passive participation works better.** Best runs keep
   books active and focus on execution quality.
3. **Product-specific treatment beats one global policy.** Confirmed
   again: regime+inventory policy table per product is the right shape.
4. **`inv_skew = 0.035` is part of strong branches.** Kept; only
   `skew_scale` lets VEX go a touch stronger.
5. **R3 replay is directional only.** No counterparty data on R3, so
   the new engine looks worse there. Use R4 rust for evaluation.
6. **Rust ≠ portal.** Portal is final truth. Rust margins under ±200
   PnL are within noise and should not be pushed against.

## Kept Evidence Runs
- `490941` (timeout failure)
- `491030` (validator/type failure)
- `491459` (stable baseline after fixes)
- `494694` (first strong branch)
- `495103` (strong branch confirmation)
- `496203` (ladder near-best)
- `496409` (current best, frozen fallback)
- `496831` (tie, last micro tweak before pivoting to overhaul)

## Method Family Outcomes
- Stabilization fixes: **required** and successful.
- Flow gating/suppression: **rejected**.
- Two-level passive ladder: **kept** as the HP/VEX baseline.
- Inventory-band state machine (S1, full cascade): **rejected** for
  monetization reasons.
- **Mark-led regime + per-product policy + inventory overlay: current
  candidate.** Rust +132 vs frozen, no day-3 regression, awaiting
  portal verdict.

## Product-Level Trend Summary
- `HYDROGEL_PACK` (HP): reliable core capture engine; benefits from
  continuity and queue quality. Regime engine mostly classifies as
  MAKER on R4, size_scale 1.05 keeps behavior near frozen.
- `VELVETFRUIT_EXTRACT` (VEX): also core; stronger skew (`skew_scale=
  1.15`) gives a small but real edge in MAKER regime.
- `VEV_4000`: edge exists but fragile; engine now skips the
  risk-increasing side automatically when an aggressor regime is
  detected.
- `VEV_4500/5000/5100/6000/6500`: keep disabled/flatten-only.
- `VEV_5200/5300/5400/5500`: secondary passive contributors only.

## Code Branch Guidance
- `R4trader.py` is the candidate.
- `R4 results/496409/496409.py` is the frozen fallback (`+909.96`).
- If a future change needs to isolate the regime engine, pull just
  `_update_mark_telemetry` + `_classify_regime` + `_policy_for` and
  bypass them by hard-coding `regime = REG_MAKER` to recover the
  near-frozen branch.

## Non-Negotiable Constraints
- Integer order prices and integer quantities.
- Trade dedupe rule: `if tts == prev_ts and sig <= prev_sig: continue`.
- Runtime bounded (no cumulative O(T^2) loops).
- Do not re-enable thin/illiquid voucher directional trading.
- Soft caps, `GROSS_VOUCHER_CAP`, `DD_KILL_SWITCH` unchanged.
- No flow-window participation gating may be reintroduced.

## What To Test Next (In Order)

### After portal verdict on the current overhaul

If overhaul beats `+909.96`:
- N1: tune `AGG_THRESHOLD` in {1.0, 2.0} (single value swap).
- N2: tune VEX `skew_scale` in {1.05, 1.25}.
- N3: enable `inside_buy/inside_sell = 1` on MAKER regime for HP only
  (queue priority while makers dominate).

If overhaul ties or regresses:
- Revert `R4trader.py` to `496409` behavior.
- Try a different telemetry input: e.g. add a "size-weighted aggressor
  imbalance" signal driving inv_skew rather than orientation, while
  leaving the rest of the policy at frozen behavior.
- Do not retry parameter sweeps around the current plateau.

## Acceptance Criteria
- Primary: portal PnL > `+909.959`.
- Secondary: no runtime/validator errors.
- Reject any variant with portal regression even if local replay improves.

## Quick Prompt Template For Next Bot
Use this when handing off:

1. Confirm the current `R4trader.py` matches the Mark-led overhaul
   description above (look for `_update_mark_telemetry`,
   `_classify_regime`, `_policy_for`, `_emit_orders`).
2. Read the latest portal result for this branch from `R4 results/`.
3. Pick exactly one change from the "What To Test Next" section that
   matches the portal verdict (beat / tie / regress).
4. Run validation:
   - `R3_TRADER=R4trader python3 ROUND_3/run_backtest.py` (smoke test).
   - `rust_backtester --trader ROUND_4/R4trader.py --dataset ROUND_4
     --day {1,2,3} --persist --artifact-mode diagnostic`.
5. Submit only if rust day-3 ≥ frozen - 200 PnL and aggregate ≥ frozen.
6. Update this file with:
   - what changed
   - rust per-day deltas vs `496409`
   - portal result vs `+909.959`
   - decision (keep / revert / next test).
