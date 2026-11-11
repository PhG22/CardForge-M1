"""End-of-run cleanup: temporary files go, project files, databases and crop captures stay."""

from pathlib import Path

from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.housekeeping import cleanup_after_run
from sorter_api.store import RunStore
from test_integration import LOW, ScriptedVision, fast_settings, wait_state


def make_project(root: Path) -> None:
    for f in ["sorter_api/api.py", "vision.toml", "data/cards.db", "data/run.db", "data/cards.db.tmp",
              "data/tmp/rpicam_123.png", "capturas/captura_1.jpg", "capturas/captura_1_dados.json",
              "relatorios/x_processada.jpg", "relatorios/sub/y.txt", "calibragem_recorte/20261111_1.jpg"]:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"x" * 100)


def test_cleanup_deletes_only_temporary_files(tmp_path):
    make_project(tmp_path)
    freed = cleanup_after_run(["data/tmp", "capturas", "relatorios", "calibragem_recorte", "data", "..", ""],
                              tmp_path)
    left = sorted(str(p.relative_to(tmp_path)).replace("\\", "/") for p in tmp_path.rglob("*") if p.is_file())
    assert left == ["calibragem_recorte/20261111_1.jpg", "data/cards.db", "data/run.db", "sorter_api/api.py",
                    "vision.toml"]
    assert freed == {"files": 6, "bytes": 600}
    assert (tmp_path / "capturas").is_dir()                      # the folders stay, empty


def test_cleanup_shrinks_the_run_db_journal(tmp_path):
    store = RunStore(tmp_path / "run.db")
    for i in range(200):
        store.create_run(f"r{i}", "2026-11-11T10:00:00.000Z")
    wal = tmp_path / "run.db-wal"
    assert wal.stat().st_size > 0
    cleanup_after_run([], tmp_path, store)
    assert wal.stat().st_size == 0 and len(store.list_runs(500)) == 200


def test_every_run_end_triggers_the_cleanup(tmp_path):
    make_project(tmp_path)
    settings = fast_settings(project_root=str(tmp_path), cleanup_dirs="capturas,relatorios")
    with TestClient(create_app(settings, vision=ScriptedVision([LOW] * 9))) as c:
        assert c.post("/run/start").status_code == 200
        wait_state(c, "idle")
    assert not any((tmp_path / "capturas").iterdir()) and not any((tmp_path / "relatorios").iterdir())
    assert (tmp_path / "data/tmp/rpicam_123.png").exists()       # not in this run's list
    assert not (tmp_path / "data/cards.db.tmp").exists() and (tmp_path / "data/cards.db").exists()
