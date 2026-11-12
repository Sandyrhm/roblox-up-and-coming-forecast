# Roblox Up-and-Coming top-10 forecaster (make.fun bounty
# roblox-up-and-coming-forecast-01).
#
# Method: Monte Carlo over each game's rank trajectory. Each game's rank is
# projected forward as a mean-reverting random walk, calibrated on the kit
# history (34 archive reads, ~6h apart):
#   - rank-change sd grows as ~2 + 2.0*sqrt(h) ranks at horizon h hours
#     (measured unconditional sd is ~2 + 3.0*sqrt(h); the fitted 2.0 is
#      narrower because dropout removes the worst movers and mean
#      reversion absorbs part of the move — a sweep over the rehearsal
#      cuts picked 2.0 over 3.5/3.0/2.5/1.5, flattening below 2.0)
#   - ranks mean-revert toward the field centre (~48): over ~24h, games
#     at ranks 1-10 fall ~3.5 places on average while games at 61-97 rise
#     ~7; time constant ~60h
#   - recent rank velocity carries only a weak signal (autocorr 0.22), so
#     it is shrunk (x0.6) and clamped
#   - games drop off the chart with a hazard that grows with rank
#     (disappearing games sit at mean rank ~79); deep games vanish fast,
#     top games almost never do
#   - playing level correlates only -0.50 with rank and playing-growth
#     slope correlates only -0.09 with the next rank change, so neither
#     is used: rank trajectory is the signal
#
# For each of 12 anchor hours (3, 9, ..., 69 — each standing for 6 hours
# of the 72-hour window) the Monte Carlo ranks every surviving game and
# counts top-10 places. Place probabilities are the counts averaged over
# anchors and draws, scaled by NAMED_MASS; the rest of each place is left
# unnamed for games that enter the chart after the cut (at a ~72h lag,
# 25% of top-10 games were not on the chart at the cut). Games whose
# Monte Carlo probability is 0 are named at p=0 where a row slot allows,
# so the unnamed share is split only among games that could actually
# take the place — never-named chart games would dilute it.
#
# Deterministic: fixed seed, stdlib only, no network.

import json
import math
import os
import random
import sys
from datetime import datetime, timezone

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# --- tuned constants (defaults; env overrides exist only for local sweeps) ---
N_DRAWS = int(os.environ.get("ROBLOX_N_DRAWS", "2000"))
NAMED_MASS = float(os.environ.get("ROBLOX_NAMED_MASS", "0.75"))
SD0 = float(os.environ.get("ROBLOX_SD0", "2.0"))
SD_RATE = float(os.environ.get("ROBLOX_SD_RATE", "2.0"))
TAU_H = float(os.environ.get("ROBLOX_TAU_H", "60.0"))
VEL_SHRINK = float(os.environ.get("ROBLOX_VEL_SHRINK", "0.6"))
VEL_CLAMP = float(os.environ.get("ROBLOX_VEL_CLAMP", "0.3"))
VEL_SAT_H = float(os.environ.get("ROBLOX_VEL_SAT_H", "24.0"))
HAZARD0 = float(os.environ.get("ROBLOX_HAZARD0", "0.002"))
HAZARD_DEEP = float(os.environ.get("ROBLOX_HAZARD_DEEP", "0.02"))
CENTER_RANK = 48.0
SEED = 12345
PLACES = 10
MAX_ROWS_PER_PLACE = 100


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() / 3600.0


def load_history(input_dir):
    path = os.path.join(input_dir, "history.ndjson")
    reads = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not row.get("ok", True):
                continue
            chart = row.get("up_and_coming") or []
            if not chart:
                continue
            reads.append((parse_time(row["at"]),
                          [(g["rank"], g["universe_id"]) for g in chart]))
    reads.sort(key=lambda r: r[0])
    return reads


def build_games(reads):
    """Per game: list of (t_hours, rank), plus last rank and velocity."""
    series = {}
    for t, chart in reads:
        for rank, uid in chart:
            series.setdefault(uid, []).append((t, rank))
    t_cut = reads[-1][0]
    last_ranks = {uid: s[-1][1] for uid, s in series.items()}
    games = []
    for uid, s in series.items():
        r0 = s[-1][1]
        t_last = s[-1][0]
        # recent rank velocity (ranks/hour) over the last ~24h of data,
        # shrunk hard and clamped: the signal is weak (autocorr ~0.22)
        v = 0.0
        if len(s) >= 2:
            t0 = max(t_last - 24.0, s[0][0])
            # earliest observation at or after t0
            seg = [p for p in s if p[0] >= t0]
            if len(seg) >= 2 and seg[-1][0] > seg[0][0]:
                v = (seg[-1][1] - seg[0][1]) / (seg[-1][0] - seg[0][0])
        v = max(-VEL_CLAMP, min(VEL_CLAMP, v * VEL_SHRINK))
        absent = (t_cut - t_last) > 0.01  # not on the last chart
        games.append({
            "uid": uid,
            "r0": r0 if not absent else min(r0 + 20, 110),
            "v": v,
            "absent": absent,
            "t_last": t_last,
        })
    return games, t_cut


def forecast(input_dir):
    with open(os.path.join(input_dir, "task.json"), "r", encoding="utf-8") as f:
        task = json.load(f)
    places = int(task.get("places", PLACES))

    reads = load_history(input_dir)
    games, _t_cut = build_games(reads)

    rng = random.Random(SEED)
    anchors = [3 + 6 * i for i in range(12)]  # 3, 9, ..., 69
    n_anchors = len(anchors)

    # counts[place][uid] = number of (draw, anchor) samples at that place
    counts = {k: {} for k in range(1, places + 1)}
    for _draw in range(N_DRAWS):
        for h in anchors:
            sd = SD0 + SD_RATE * math.sqrt(h)
            rev = math.exp(-h / TAU_H)
            drift_h = min(h, VEL_SAT_H)
            projected = []
            for g in games:
                # dropout hazard grows with rank; absent games vanish faster
                hz = HAZARD0 + HAZARD_DEEP * (g["r0"] / 97.0) ** 2
                if g["absent"]:
                    hz *= 2.0
                if rng.random() < 1.0 - math.exp(-hz * h):
                    continue
                mu = CENTER_RANK + (g["r0"] - CENTER_RANK) * rev + g["v"] * drift_h
                r = mu + rng.gauss(0.0, sd)
                if r < 1.0:
                    r = 1.0
                projected.append((r, g["uid"]))
            projected.sort(key=lambda p: p[0])
            for k in range(1, min(places, len(projected)) + 1):
                uid = projected[k - 1][1]
                counts[k][uid] = counts[k].get(uid, 0) + 1

    total = N_DRAWS * n_anchors
    probabilities = []
    for k in range(1, places + 1):
        ck = counts[k]
        # rank games by MC count; p>0 first, then p=0 fillers by climb
        # potential (higher = more likely to reach the top: low last rank,
        # rising velocity). Row cap is 100 per place.
        ranked = sorted(ck.items(), key=lambda kv: -kv[1])
        rows = []
        for uid, c in ranked:
            if len(rows) >= MAX_ROWS_PER_PLACE:
                break
            rows.append((uid, c / total * NAMED_MASS))
        if len(rows) < MAX_ROWS_PER_PLACE:
            named = {uid for uid, _p in rows}
            fillers = sorted(
                (g for g in games if g["uid"] not in named),
                key=lambda g: (g["r0"] - g["v"] * 24.0),
            )
            for g in fillers:
                if len(rows) >= MAX_ROWS_PER_PLACE:
                    break
                rows.append((g["uid"], 0.0))
        for uid, p in rows:
            probabilities.append({"universe_id": uid, "place": k, "p": round(p, 6)})

    # top10 = argmax per place, distinct, in order
    top10 = []
    for k in range(1, places + 1):
        ck = counts[k]
        if not ck:
            continue
        for uid, _c in sorted(ck.items(), key=lambda kv: -kv[1]):
            if uid not in top10:
                top10.append(uid)
                break
    # fill any missing places with the best remaining games by MC mass
    if len(top10) < places:
        overall = {}
        for k in range(1, places + 1):
            for uid, c in counts[k].items():
                overall[uid] = overall.get(uid, 0) + c
        for uid, _c in sorted(overall.items(), key=lambda kv: -kv[1]):
            if len(top10) >= places:
                break
            if uid not in top10:
                top10.append(uid)

    return {"probabilities": probabilities, "top10": top10[:places]}


def main():
    out = forecast(INPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(INPUT_DIR, "task.json"), "r", encoding="utf-8") as f:
        task = json.load(f)
    out_path = os.path.join(OUTPUT_DIR, task.get("output", "forecast.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
