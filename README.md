# Roblox Up-and-Coming top-10 forecaster

Forecasts Roblox's Up-and-Coming chart top 10 for every hour of the 72
hours after entries close, from the chart's hourly snapshots. Entry for
make.fun bounty `roblox-up-and-coming-forecast-01` (account
`@golden-falcon-20`).

## Method

`main.py` (Python 3.13, standard library only, deterministic) runs a
fixed-seed Monte Carlo over each game's rank trajectory:

- **Rank projection.** Each game's rank is projected forward as a
  mean-reverting random walk, calibrated on the kit history (34 archive
  reads, ~6 h apart): rank-change sd grows as ~2 + 2.0·√h ranks at
  horizon h hours (measured unconditional sd is ~2 + 3.0·√h; the
  fitted 2.0 is narrower because dropout removes the worst movers and
  mean reversion absorbs part of the move — a sweep over the rehearsal
  cuts picked 2.0, flattening below it); ranks mean-revert toward the
  field centre (~48) with a ~60 h time constant; recent rank velocity
  is shrunk (×0.6) and clamped, because it carries only a weak signal
  (autocorr ~0.22).
- **Dropout.** Games leave the chart with a hazard that grows with
  rank (disappearing games sit at mean rank ~79); deep games vanish
  fast, top games almost never do.
- **What is not used.** Playing level correlates only −0.50 with rank
  and playing-growth slope only −0.09 with the next rank change, so
  neither is used: rank trajectory is the signal. Games' `details`
  (including age) are absent from the archive reads, so no age feature
  is used either.
- **Averaging over the window.** For each of 12 anchor hours (3, 9, …,
  69 — each standing for 6 hours of the 72-hour window) the Monte
  Carlo ranks every surviving game and counts top-10 places. Place
  probabilities are the counts averaged over anchors and draws, scaled
  by a named mass of 0.75 (a sweep over the rehearsal cuts picked 0.75
  over 0.55/0.65; the rest of each place is left unnamed for games that
  enter the chart after the cut — at a ~72 h lag, 25% of top-10 games
  were not on the chart at the cut).
- **p=0 naming.** Games whose Monte Carlo probability is 0 are named at
  p=0 where a row slot allows (100 rows per place), so the unnamed share
  is split only among games that could actually take the place.

## Validation (rehearsal backtest)

The kit holds no answer data, so the check is a backtest: cut the
history at a read, forecast, and score the later reads with a local
replica of the competition's log score (ln p per place, floor 1e-4).
The house persistence baseline is scored the same way at the same cuts.

| cut | answers | ours | baseline | gain |
|---|---|---|---|---|
| read 14 | 19 | -46.20 | -53.96 | +7.76 |
| read 18 | 15 | -42.94 | -52.11 | +9.17 |
| read 22 | 11 | -43.40 | -55.85 | +12.45 |
| read 26 | 7 | -36.56 | -50.78 | +14.22 |
| **mean** | | **-42.3** | **-53.2** | **+10.9** |

Calibration sweep (same 4 cuts, mean score): named mass 0.55 −44.7 /
0.65 −44.0 / 0.75 −43.7; spread rate 3.5 −44.9 / 3.0 −44.0 / 2.5 −43.0 /
2.0 −42.5 / 1.5 −42.5; velocity shrink 0 −44.4 / 0.3 −44.0 / 0.6 −43.9.
Shipped constants (0.75 / 2.0 / 0.6) combine the three, mean −42.3,
winning on every cut vs the pre-calibration defaults. The spread curve
flattened below 2.0, so 2.0 is the safer edge of the plateau rather
than the extreme 1.5 (less floor risk on the real 72 h window).

Two runs on the same history write byte-identical output; a full-kit
run takes ~6 s and writes ~57 KB (limits: 300 s, 1 MB).

## Honest limits

- The rehearsal answers are ~6 h apart, not hourly, and its longest
  horizons (~114 h) run past the final's 72 h window — a harsher test
  than the final, not a replica of it.
- The rehearsal gain is measured on one archive (34 reads); the final
  window is a different 72 hours with a different chart. The +10.9 is
  a backtest, not a promise — and the calibration constants were fitted
  on the same single archive, so part of the +1.7 calibration gain may
  be in-sample luck rather than real improvement.
- The house AI forecast's score is unknown; beating the persistence
  baseline is necessary but not sufficient for a prize.
- The model cannot foresee games that are published after the cut and
  explode: those are covered only by the unnamed share.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Writes `out/forecast.json`. Two runs on the same history write the same
bytes.
