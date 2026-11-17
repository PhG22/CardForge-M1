"""cards_to_improve/: a dataset of the cards that took too long to identify.

Every card whose identification (first photo -> decision, retries included) takes longer
than `slow_card_s` (3 s) is kept, to find out what makes those cards slow:

    cards_to_improve/<date>-<time>_<run8>/            one folder per run (only if it had a slow card)
        card_007/
            captura_1_original.jpg                    the camera picture of each capture (RF29)
            captura_1_recorte_anotado.jpg             the straightened card with the ROIs read
            captura_2_original.jpg ...                and its status / confidence
            resultado.json                            times (feed, each capture: light, photo,
                                                      recognition, OCR calls; base, drop, total),
                                                      what each capture read and matched, the
                                                      final decision, first-try and why not
        indice.csv                                    one line per slow card (sort it in Excel)
        resumo.json                                   the run, the limit, how many cards, MB used

The pictures are written by a background thread while the base turns and the card drops
(the CPU is idle then), so a slow card does not make the next one slower; the index and
summary of the run are written when the run ends. A full camera picture is ~24 MB in RAM
(IMX219, 3280x2464), so only one slow card waits for the writer at a time (~2 s of work on
the Pi 3B, less than the next card's cycle): if the writer is still busy with the previous
slow card (or the folder is over `max_mb`), the card keeps only its resultado.json. The
writer runs at the lowest CPU priority (nice 19). The folder is never deleted by the
end-of-run cleanup (housekeeping.PROTECTED): delete old runs by hand when done with them.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

INDEX_COLUMNS = ["card_number", "identify_s", "total_s", "attempts", "ocr_calls", "scan_status", "match_method",
                 "confidence", "card_name", "set_code", "collector_number", "first_try", "fallbacks", "photos",
                 "folder"]


@dataclass
class _CardJob:
    number: int
    folder: Path
    traces: list                      # identifier.CaptureTrace (pictures dropped once written)
    captures: list[dict]              # the traces as JSON (computed before the pictures go)
    photos: bool = True               # False = no pictures for this card (queue full / folder full)
    photos_note: str = ""
    files: dict = field(default_factory=dict)   # attempt -> {"original": ..., "recorte_anotado": ...}
    bytes: int = 0
    done: Optional[threading.Event] = None


class SlowCardRecorder:
    def __init__(self, folder: str | Path, threshold_s: float = 3.0, max_mb: float = 1000.0,
                 max_pending: int = 1) -> None:
        self.folder = Path(folder)
        self.threshold_s = threshold_s
        self.max_bytes = max_mb * 1e6
        self._queue: queue.Queue = queue.Queue()
        self._pending = threading.BoundedSemaphore(max(1, max_pending))   # cards with pictures in RAM
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._run: Optional[dict] = None
        self._run_dir: Optional[Path] = None
        self._rows: list[dict] = []
        self._used = 0.0                    # bytes in self.folder (measured at the start of each run)
        self._written = 0                   # bytes written in this run
        self._skipped = 0                   # slow cards saved without pictures in this run
        self._full_warned = False

    @property
    def enabled(self) -> bool:
        return self.threshold_s > 0

    def is_slow(self, identify_s: Optional[float]) -> bool:
        return self.enabled and identify_s is not None and identify_s > self.threshold_s

    # ------------------------------------------------------------- run

    def start_run(self, run_id: str, started_at: str) -> None:
        if not self.enabled:
            return
        stamp = started_at.replace("-", "").replace(":", "")[:15].replace("T", "-")
        with self._lock:
            self._run = {"run_id": run_id, "started_at": started_at}
            self._run_dir = self.folder / f"{stamp}_{run_id[:8]}"
            self._rows, self._written, self._skipped, self._full_warned = [], 0, 0, False
        try:
            self._used = sum(f.stat().st_size for f in self.folder.rglob("*") if f.is_file()) \
                if self.folder.is_dir() else 0.0
        except OSError:
            self._used = 0.0

    def add_card(self, number: int, identification, identify_s: float) -> Optional[_CardJob]:
        """Right after the identification of a slow card: queue its pictures. Never blocks."""
        if self._run_dir is None or not self.is_slow(identify_s):
            return None
        traces = list(getattr(identification, "trace", ()) or ())
        job = _CardJob(number, self._run_dir / f"card_{number:03d}", traces, [t.to_dict() for t in traces])
        has_pictures = any(t.frame is not None or t.rectified is not None for t in traces)
        if not has_pictures:
            job.photos, job.photos_note = False, "no pictures (no camera)"
        elif self._used + self._written >= self.max_bytes:
            job.photos, job.photos_note = False, f"cards_to_improve is over {self.max_bytes / 1e6:.0f} MB"
            if not self._full_warned:
                self._full_warned = True
                log.warning("cards_to_improve is over %.0f MB: slow cards are saved without pictures "
                            "(delete old runs from %s)", self.max_bytes / 1e6, self.folder)
        elif not self._pending.acquire(blocking=False):
            job.photos, job.photos_note = False, "writer busy: pictures not saved"
        if not job.photos:
            for t in traces:
                t.drop_images()
            if has_pictures:
                self._skipped += 1
        else:
            self._submit(("photos", job))
        return job

    def finish_card(self, job: Optional[_CardJob], result: dict) -> None:
        """After the drop: the card's times and decision (resultado.json + a line of indice.csv)."""
        if job is None:
            return
        self._submit(("result", (job, result)))

    def end_run(self, run: dict, timeout_s: float = 30.0) -> Optional[Path]:
        """The run ended: wait for the pending pictures, then write indice.csv and resumo.json."""
        if self._run_dir is None:
            return None
        done = threading.Event()
        self._submit(("end", (run, done)))
        if not done.wait(timeout_s):
            log.warning("cards_to_improve: the writer did not finish in %.0f s", timeout_s)
        with self._lock:
            run_dir, rows = self._run_dir, list(self._rows)
            self._run_dir, self._run = None, None
        return run_dir if rows else None

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    # ------------------------------------------------------------- writer thread

    def _submit(self, item) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="slow-cards-writer", daemon=True)
            self._thread.start()
        self._queue.put(item)

    def _loop(self) -> None:
        # Lowest CPU priority for this thread only (Linux: nice is per thread): the JPEG encoding
        # of a full camera picture must never slow down the OCR of the next card.
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
        except (AttributeError, OSError):
            pass                                 # Windows / not allowed: normal priority
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, payload = item
            try:
                if kind == "photos":
                    self._write_photos(payload)
                elif kind == "result":
                    self._write_result(*payload)
                elif kind == "end":
                    self._write_summary(*payload)
            except Exception:                    # never stop a run because of the dataset
                log.exception("cards_to_improve: could not write %s", kind)
            finally:
                if kind == "photos":
                    for t in payload.traces:
                        t.drop_images()
                    self._pending.release()
                elif kind == "end":
                    payload[1].set()

    def _write_photos(self, job: _CardJob) -> None:
        import cv2

        from cardforge.cv.report import annotate

        job.folder.mkdir(parents=True, exist_ok=True)
        for t in job.traces:
            names = {}
            if t.frame is not None:
                names["original"] = f"captura_{t.attempt}_original.jpg"
                cv2.imwrite(str(job.folder / names["original"]), t.frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if t.result is not None and getattr(t.result, "rectified", None) is not None:
                names["recorte_anotado"] = f"captura_{t.attempt}_recorte_anotado.jpg"
                cv2.imwrite(str(job.folder / names["recorte_anotado"]), annotate(t.result),
                            [cv2.IMWRITE_JPEG_QUALITY, 90])
            job.files[t.attempt] = names
        job.bytes = sum(f.stat().st_size for f in job.folder.iterdir() if f.is_file())
        self._written += job.bytes

    def _write_result(self, job: _CardJob, result: dict) -> None:
        job.folder.mkdir(parents=True, exist_ok=True)
        captures = [{**c, "files": job.files.get(c["attempt"], {})} for c in job.captures]
        body = {"run": self._run, "card_number": job.number,
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "slow_card_s": self.threshold_s, **result, "captures": captures,
                "photos": job.photos, "photos_note": job.photos_note or None}
        path = job.folder / "resultado.json"
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        self._written += path.stat().st_size
        outcome = result.get("outcome") or {}
        row = {"card_number": job.number, "identify_s": result.get("identify_s"), "total_s": result.get("total_s"),
               "attempts": outcome.get("attempts"),
               "ocr_calls": sum(c.get("ocr_calls") or 0 for c in job.captures),
               "scan_status": outcome.get("scan_status"), "match_method": outcome.get("match_method"),
               "confidence": outcome.get("confidence"), "card_name": outcome.get("card_name"),
               "set_code": outcome.get("set_code"), "collector_number": outcome.get("collector_number"),
               "first_try": int(bool(result.get("first_try"))), "fallbacks": " ".join(result.get("fallbacks") or ()),
               "photos": int(job.photos), "folder": job.folder.name}
        with self._lock:
            self._rows.append(row)

    def _write_summary(self, run: dict, done: threading.Event) -> None:
        with self._lock:
            run_dir, rows = self._run_dir, list(self._rows)
        if run_dir is None or not rows:
            return
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "indice.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["card_number"]))
        summary = {"run": run, "slow_card_s": self.threshold_s, "slow_cards": len(rows),
                   "cards_processed": run.get("cards_processed"), "without_pictures": self._skipped,
                   "megabytes": round(self._written / 1e6, 1),
                   "slowest": sorted(rows, key=lambda r: -(r["identify_s"] or 0))[:5]}
        (run_dir / "resumo.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("cards_to_improve: %d slow card(s) of run %s saved in %s (%.1f MB)", len(rows),
                 run.get("run_id"), run_dir, self._written / 1e6)
