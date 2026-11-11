"""Files on the Raspberry Pi's SD card.

* CropCaptureLog: every picture taken to calibrate the fixed crop is kept in
  calibragem_recorte/ (original + annotated + one CSV line), so a bad crop can be
  debugged later from the real machine's photos. Never deleted automatically.
* cleanup_after_run(): at the end of every run, delete temporary files (pictures and
  reports from the bench commands, leftovers of interrupted downloads/captures) and
  shrink run.db's write-ahead log. Project files, data/cards.db, data/run.db and
  calibragem_recorte/ are never touched.

The run loop itself keeps the card pictures in memory only: nothing is written to
disk per card except its row in run.db.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_COLUMNS = ["file", "taken_at", "found", "strategy", "corners", "frame_width", "frame_height"]


def project_path(path: str | Path, root: Path = PROJECT_ROOT) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p


class CropCaptureLog:
    """Saves the fixed-crop calibration pictures in calibragem_recorte/."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def save_sample(self, image: np.ndarray, corners: Optional[list], strategy: Optional[str],
                    pixel_corners: Optional[np.ndarray]) -> str:
        """Original (full resolution), annotated copy and a line in medicoes.csv. Returns the file name."""
        self.folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stem = f"{now:%Y%m%d_%H%M%S}_{now.microsecond // 1000:03d}" + ("" if corners else "_sem_borda")
        cv2.imwrite(str(self.folder / f"{stem}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        annotated = image.copy()
        if pixel_corners is not None:
            cv2.polylines(annotated, [pixel_corners.astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0),
                          max(2, image.shape[1] // 300))
        cv2.imwrite(str(self.folder / f"{stem}_anotada.jpg"), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self._append_csv({"file": f"{stem}.jpg", "taken_at": now.isoformat(timespec="seconds"),
                          "found": int(corners is not None), "strategy": strategy or "",
                          "corners": json.dumps(corners) if corners else "",
                          "frame_width": image.shape[1], "frame_height": image.shape[0]})
        return f"{stem}.jpg"

    def save_result(self, summary: dict, vision_toml: str) -> str:
        """The crop that was saved, with the measurements it came from."""
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"recorte_salvo_{datetime.now():%Y%m%d_%H%M%S}.json"
        (self.folder / name).write_text(json.dumps({**summary, "vision_toml": vision_toml}, indent=2),
                                        encoding="utf-8")
        return name

    def _append_csv(self, row: dict) -> None:
        path = self.folder / "medicoes.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if new:
                writer.writeheader()
            writer.writerow(row)


# Never deleted by the cleanup, whatever the settings say.
PROTECTED = ("calibragem_recorte", "data/cards.db", "data/run.db", "vision.toml")


def cleanup_after_run(dirs: list[str], root: Path = PROJECT_ROOT, store=None) -> dict:
    """Delete the contents of the temporary folders (inside the project only) and stale *.tmp files
    in data/, then compact run.db's journal. Returns what was freed (for the log)."""
    root = root.resolve()
    protected = [(root / p).resolve() for p in PROTECTED]
    freed, files = 0, 0

    def remove(path: Path) -> None:
        nonlocal freed, files
        if any(path == p or p in path.parents or path in p.parents for p in protected):
            return
        size = path.stat().st_size if path.is_file() else sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        count = 1 if path.is_file() else sum(1 for f in path.rglob("*") if f.is_file())
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        freed, files = freed + size, files + count

    for d in dirs:
        folder = project_path(d.strip(), root).resolve()
        if not d.strip() or folder == root or root not in folder.parents or not folder.is_dir():
            continue                                    # only folders inside the project
        for item in folder.iterdir():
            try:
                remove(item)
            except OSError as exc:
                log.warning("cleanup: could not delete %s: %s", item, exc)
    for stale in (root / "data").glob("*.tmp"):        # e.g. cards.db.tmp from an interrupted sync
        try:
            remove(stale)
        except OSError as exc:
            log.warning("cleanup: could not delete %s: %s", stale, exc)
    if store is not None:
        store.compact()
    if files:
        log.info("cleanup after run: %d temporary file(s) deleted, %.1f MB freed", files, freed / 1e6)
    return {"files": files, "bytes": freed}
