"""Card processing benchmark of each run (saved when the run ends).

Two times per card, in seconds:
* identify_s: the identification, from the first photo to the decision, retries included
              (light change + photo + crop + OCR + match, RF29: up to 3 captures)
* total_s:    the whole card cycle, from the start of its feed to the end of its drop (RNF22)

Two groups:
* general:    every card processed in the run
* first_try:  only the cards read at the first try with no failsafe: identified (or found
              face down) at the first capture, with the minimum OCR (no 180° title re-read,
              no layout OCR, no face-down check after a failed identification) and fed at
              the first pull (RF05). Each card that is not first-try says why (fallbacks).

Statistics: n, mean, median, mode (of the times rounded to 0.1 s; with a tie, the
shortest; mode_count = how many cards have it), min and max.

Saved in run.db (table run_benchmark, GET /runs/{id}) and in benchmarks/:
    benchmarks/benchmark_runs.csv          one line per run, group and time (all runs: easy to compare)
    benchmarks/<date>_<run8>.json          the same + every card's times and fallbacks
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
from collections import Counter
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

GROUPS = ("general", "first_try")
METRICS = ("identify_s", "total_s")
STATS = ("n", "mean", "median", "mode", "mode_count", "min", "max")
MODE_RESOLUTION_S = 0.1
CSV_COLUMNS = ["run_id", "started_at", "stopped_at", "status", "group", "metric", *STATS]

# Why a card is not first-try (CaptureTrace / VisionIdentifier / controller)
FALLBACK_REASONS = {
    "retry": "more than one capture (RF29)",
    "orientation_reread": "title also read upside down (180°)",
    "layout_ocr": "layout OCR: the standard read did not settle the card",
    "face_down_after_identify": "face-down check after a failed identification",
    "not_identified": "unidentified after all captures",
    "vision_error": "camera / vision error in a capture",
    "feed_retry": "second pull of the feed (RF05)",
}


def describe(values: Iterable[float | None]) -> dict:
    """n, mean, median, mode, mode_count, min, max of the values (None = not measured, skipped)."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {k: (0 if k in ("n", "mode_count") else None) for k in STATS}
    binned = [round(v, 1) for v in vals]
    mode = min(statistics.multimode(binned))
    return {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "mode": mode,
        "mode_count": binned.count(mode),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
    }


def run_benchmark(cards: list[dict]) -> dict:
    """cards: one dict per card with identify_s, total_s, first_try and fallbacks."""
    rows = {"general": cards, "first_try": [c for c in cards if c.get("first_try")]}
    reasons = Counter(r for c in cards for r in c.get("fallbacks") or ())
    return {
        "cards": len(cards),
        "first_try_cards": len(rows["first_try"]),
        "first_try_share": round(len(rows["first_try"]) / len(cards), 3) if cards else None,
        "fallbacks": dict(reasons.most_common()),
        **{group: {metric: describe(c.get(metric) for c in rows[group]) for metric in METRICS}
           for group in GROUPS},
    }


def benchmark_rows(bench: dict) -> list[dict]:
    """One row per group and metric (run.db table and CSV)."""
    return [{"group": group, "metric": metric, **bench[group][metric]} for group in GROUPS for metric in METRICS]


def summary_text(bench: dict) -> str:
    """One line for the log: mean / median of the identification, general and first-try."""
    parts = []
    for group in GROUPS:
        s = bench[group]["identify_s"]
        if s["n"]:
            parts.append(f"{group} n={s['n']} identify mean {s['mean']:.2f} s median {s['median']:.2f} s "
                         f"mode {s['mode']:.1f} s min {s['min']:.2f} s max {s['max']:.2f} s")
    return "; ".join(parts) or "no cards"


class BenchmarkFiles:
    """benchmarks/: a CSV with every run and one JSON per run."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def write(self, run: dict, bench: dict, cards: list[dict]) -> Path:
        """run: run_id, started_at, stopped_at, status. Returns the JSON file."""
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = (run.get("started_at") or "").replace("-", "").replace(":", "")[:15].replace("T", "-")
        path = self.folder / f"{stamp or 'run'}_{run['run_id'][:8]}.json"
        path.write_text(json.dumps({"run": run, "definitions": {
            "identify_s": "first photo -> decision, retries included",
            "total_s": "start of the feed -> end of the drop",
            "first_try": "identified or face down at the first capture, minimum OCR, first feed pull",
            "mode": f"most common time rounded to {MODE_RESOLUTION_S} s (tie: the shortest)",
            "fallbacks": FALLBACK_REASONS}, "benchmark": bench, "cards": cards}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        csv_path = self.folder / "benchmark_runs.csv"
        new = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            if new:
                writer.writeheader()
            for row in benchmark_rows(bench):
                writer.writerow({**{k: run.get(k) for k in ("run_id", "started_at", "stopped_at", "status")}, **row})
        return path
