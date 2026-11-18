"""Card times of a run: the benchmark (general / first-try) and the cards_to_improve dataset."""

import csv
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cardforge.cv import IdentificationResult, IdentificationStatus, MatchMethod
from sorter_api.api import create_app
from sorter_api.benchmark import describe, run_benchmark
from sorter_api.housekeeping import cleanup_after_run
from sorter_api.identifier import VisionIdentifier, capture_fallbacks
from test_integration import card, fast_settings, wait_state


def result(status=IdentificationStatus.IDENTIFIED, c=None, ocr_calls=1, pictures=True, **debug):
    r = IdentificationResult(status, card=c, method=MatchMethod.SET_NUMBER if c else MatchMethod.NONE,
                             confidence=100 if c else 0)
    r.debug.update(ocr_calls=ocr_calls, capture_ms=40, **debug)
    r.elapsed_ms = 900
    if pictures:
        r.frame = np.full((120, 160, 3), 90, np.uint8)
        r.rectified = np.full((880, 630, 3), 200, np.uint8)
    return r


class TimedVision:
    """Scripted results, each after its own delay (a slow recognition)."""
    ready = True

    def __init__(self, steps):
        self.steps = list(steps)              # (delay_s, result or exception)

    def identify(self):
        delay, outcome = self.steps.pop(0) if self.steps else (0.0, result(IdentificationStatus.LOW_CONFIDENCE))
        time.sleep(delay)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ------------------------------------------------------------------ statistics

def test_describe_gives_mean_median_mode_min_max():
    s = describe([1.0, 1.04, 1.5, 2.0, None])
    assert s == {"n": 4, "mean": 1.385, "median": 1.27, "mode": 1.0, "mode_count": 2, "min": 1.0, "max": 2.0}
    assert describe([2.0, 1.0])["mode"] == 1.0                   # tie: the shortest
    assert describe([None]) == {"n": 0, "mean": None, "median": None, "mode": None, "mode_count": 0,
                                "min": None, "max": None}


def test_benchmark_splits_general_and_first_try():
    cards = [{"identify_s": 1.2, "total_s": 4.0, "first_try": True, "fallbacks": []},
             {"identify_s": 1.4, "total_s": 4.4, "first_try": True, "fallbacks": []},
             {"identify_s": 5.1, "total_s": 8.0, "first_try": False, "fallbacks": ["retry", "orientation_reread"]},
             {"identify_s": 2.2, "total_s": 5.0, "first_try": False, "fallbacks": ["orientation_reread"]}]
    b = run_benchmark(cards)
    assert b["cards"] == 4 and b["first_try_cards"] == 2 and b["first_try_share"] == 0.5
    assert b["general"]["identify_s"]["n"] == 4 and b["general"]["identify_s"]["max"] == 5.1
    assert b["first_try"]["identify_s"] == describe([1.2, 1.4])
    assert b["first_try"]["total_s"]["mean"] == 4.2
    assert b["fallbacks"] == {"orientation_reread": 2, "retry": 1}


@pytest.mark.parametrize("debug, reasons", [
    ({"ocr_calls": 1}, ()),                                                  # the shortest path
    ({"ocr_calls": 0, "face_down_check": "brown_only"}, ()),                 # face down, told by color
    ({"ocr_calls": 2, "score_rotated": 40}, ("orientation_reread",)),
    ({"ocr_calls": 2}, ("layout_ocr",)),
    ({"ocr_calls": 4, "score_rotated": 40}, ("orientation_reread", "layout_ocr")),
    ({"ocr_calls": 3, "face_down_check": "after_identify"}, ("layout_ocr", "face_down_after_identify")),
])
def test_failsafes_of_one_capture(debug, reasons):
    assert capture_fallbacks(debug) == reasons


def test_identifier_traces_every_capture():
    whip = card("Whip", "")
    vision = TimedVision([(0, result(IdentificationStatus.LOW_CONFIDENCE, ocr_calls=3, score_rotated=10)),
                          (0, RuntimeError("camera timeout")),
                          (0, result(c=whip))])
    out = VisionIdentifier(vision, attempts=3).identify(lambda s: None)
    assert out.scan_status == "identified" and out.attempts == 3
    assert out.fallbacks == ("retry", "orientation_reread", "layout_ocr", "vision_error")
    assert [t.status for t in out.trace] == ["LOW_CONFIDENCE", "ERROR", "IDENTIFIED"]
    assert [t.light for t in out.trace] == [60, 80, 100]                          # RNF09
    assert out.trace[2].recognition_ms == 900 and out.trace[2].frame is not None
    assert out.trace[1].to_dict()["error"] == "camera timeout"

    first = VisionIdentifier(TimedVision([(0, result(c=whip))])).identify(lambda s: None)
    assert first.fallbacks == () and len(first.trace) == 1
    lost = VisionIdentifier(TimedVision([]), attempts=2).identify(lambda s: None)
    assert lost.scan_status == "unidentified" and lost.fallbacks == ("retry", "not_identified")


# ------------------------------------------------------------------ during a run

def test_run_saves_benchmark_and_slow_cards(tmp_path):
    slow_dir, bench_dir = tmp_path / "cards_to_improve", tmp_path / "benchmarks"
    plains, forest, island = card("Plains", "W"), card("Forest", "G"), card("Island", "U")
    vision = TimedVision([
        (0.0, result(c=plains)),                                                  # 1: first try
        (0.35, result(IdentificationStatus.LOW_CONFIDENCE, ocr_calls=2, score_rotated=30)),
        (0.0, result(c=forest)),                                                  # 2: slow, retried
        (0.0, result(c=island, ocr_calls=2, score_rotated=40, rotated180=True)),  # 3: upside down
    ])
    settings = fast_settings(sim_stack_cards=3, slow_card_s=0.3, slow_cards_dir=str(slow_dir),
                             benchmark_dir=str(bench_dir))
    with TestClient(create_app(settings, vision=vision)) as c:
        assert c.post("/run/start").status_code == 200
        run = wait_state(c, "idle")["run"]
        res = c.get(f"/runs/{run['run_id']}").json()
        timing = c.get("/run/status").json()["timing"]

    # per card in run.db
    cards = res["cards"]
    assert [x["first_try"] for x in cards] == [1, 0, 0]
    assert [x["fallbacks"] for x in cards] == ["", "retry orientation_reread", "orientation_reread"]
    assert cards[1]["identify_s"] >= 0.35 and all(x["total_s"] >= x["identify_s"] for x in cards)

    # the benchmark: run.db, the API and benchmarks/
    bench = res["benchmark"]
    assert bench["general"]["identify_s"]["n"] == 3 and bench["first_try"]["identify_s"]["n"] == 1
    assert bench["general"]["identify_s"]["max"] == cards[1]["identify_s"]
    assert set(bench["general"]["total_s"]) == {"n", "mean", "median", "mode", "mode_count", "min", "max"}
    assert timing["first_try_cards"] == 1 and timing["benchmark"]["cards"] == 3
    rows = list(csv.DictReader(open(bench_dir / "benchmark_runs.csv", encoding="utf-8")))
    assert [(r["group"], r["metric"]) for r in rows] == [("general", "identify_s"), ("general", "total_s"),
                                                         ("first_try", "identify_s"), ("first_try", "total_s")]
    assert all(r["run_id"] == run["run_id"] for r in rows)
    detail = json.loads(next(bench_dir.glob(f"*_{run['run_id'][:8]}.json")).read_text(encoding="utf-8"))
    assert detail["benchmark"]["first_try_cards"] == 1 and len(detail["cards"]) == 3

    # cards_to_improve: only the slow card, with its 2 captures
    (run_dir,) = list(slow_dir.iterdir())
    assert run_dir.name.endswith(run["run_id"][:8])
    folder = run_dir / "card_002"
    assert sorted(p.name for p in folder.iterdir()) == [
        "captura_1_original.jpg", "captura_1_recorte_anotado.jpg",
        "captura_2_original.jpg", "captura_2_recorte_anotado.jpg", "resultado.json"]
    data = json.loads((folder / "resultado.json").read_text(encoding="utf-8"))
    assert data["identify_s"] >= 0.35 and data["first_try"] is False
    assert data["outcome"]["card_name"] == "Forest" and data["outcome"]["tray"] == 5
    assert [c["status"] for c in data["captures"]] == ["LOW_CONFIDENCE", "IDENTIFIED"]
    assert data["captures"][0]["files"]["original"] == "captura_1_original.jpg"
    assert data["captures"][0]["recognition_ms"] == 900 and data["phases"]["total_s"] == data["total_s"]
    index = list(csv.DictReader(open(run_dir / "indice.csv", encoding="utf-8")))
    assert [(r["card_number"], r["card_name"], r["ocr_calls"]) for r in index] == [("2", "Forest", "3")]
    summary = json.loads((run_dir / "resumo.json").read_text(encoding="utf-8"))
    assert summary["slow_cards"] == 1 and summary["run"]["cards_processed"] == 3


def test_no_slow_card_no_folder(tmp_path):
    vision = TimedVision([(0, result(c=card("Plains", "W")))] * 2)
    settings = fast_settings(sim_stack_cards=2, slow_card_s=5, slow_cards_dir=str(tmp_path / "slow"),
                             benchmark_dir=str(tmp_path / "bench"))
    with TestClient(create_app(settings, vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
    assert not (tmp_path / "slow").exists()
    assert (tmp_path / "bench" / "benchmark_runs.csv").exists()


def test_slow_card_without_room_keeps_only_its_results(tmp_path):
    slow_dir = tmp_path / "slow"
    (slow_dir / "old_run").mkdir(parents=True)
    (slow_dir / "old_run" / "big.jpg").write_bytes(b"x" * 2_000_000)
    vision = TimedVision([(0.2, result(c=card("Plains", "W")))])
    settings = fast_settings(sim_stack_cards=1, slow_card_s=0.1, slow_cards_dir=str(slow_dir), slow_cards_max_mb=1,
                             benchmark_dir=str(tmp_path / "bench"))
    with TestClient(create_app(settings, vision=vision)) as c:
        c.post("/run/start")
        wait_state(c, "idle")
    (folder,) = [p for p in slow_dir.glob("*/card_001")]
    assert [p.name for p in folder.iterdir()] == ["resultado.json"]
    data = json.loads((folder / "resultado.json").read_text(encoding="utf-8"))
    assert data["photos"] is False and "over 1 MB" in data["photos_note"]


def test_end_of_run_cleanup_keeps_the_dataset_and_benchmarks(tmp_path):
    for f in ["cards_to_improve/r1/card_001/resultado.json", "benchmarks/benchmark_runs.csv",
              "meus_lentos/x.json", "capturas/a.jpg"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    cleanup_after_run(["cards_to_improve", "benchmarks", "meus_lentos", "capturas"], tmp_path, keep=("meus_lentos",))
    left = sorted(str(p.relative_to(tmp_path)).replace("\\", "/") for p in tmp_path.rglob("*") if p.is_file())
    assert left == ["benchmarks/benchmark_runs.csv", "cards_to_improve/r1/card_001/resultado.json",
                    "meus_lentos/x.json"]
