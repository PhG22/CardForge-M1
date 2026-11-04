"""
Ferramentas de linha de comando do subsistema de visão.

    python -m cardforge.cv sync [--force]                     # cria/atualiza o cards.db (precisa de rede)
    python -m cardforge.cv identify foto.jpg [...]            # identifica imagens e imprime JSON
    python -m cardforge.cv batch input_cartas --out relatorios [--truth gabarito.csv]
    python -m cardforge.cv camera [-n 5]                      # Raspberry Pi: captura e identifica
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
from pathlib import Path

import cv2

from ..data.repository import normalize_number
from .config import VisionConfig
from .matching.name_index import compact
from .report import write_report
from .result import IdentificationResult, IdentificationStatus
from .vision import VisionModule

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _module(args, camera=None) -> VisionModule:
    v = VisionModule(VisionConfig.load(args.config), camera=camera)
    v.start(open_camera=camera is None and args.cmd == "camera")
    return v


def cmd_sync(args) -> int:
    v = VisionModule(VisionConfig.load(args.config), camera=_NoCamera())
    print(v.update_database(force=args.force))
    return 0


def cmd_identify(args) -> int:
    v = _module(args, camera=_NoCamera())
    for path in args.images:
        image = cv2.imread(path)
        if image is None:
            print(f"{path}: cannot read image", file=sys.stderr)
            continue
        print(json.dumps(v.recognize(image).to_dict() | {"source": path}, ensure_ascii=False, indent=2))
    return 0


def _load_truth(path: str) -> dict[str, dict]:
    """CSV com colunas file, set, number e/ou name (uma linha por imagem)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {Path(row["file"]).name: row for row in csv.DictReader(f)}


def _is_correct(r: IdentificationResult, truth: dict) -> bool:
    if not r.identified or r.card is None:
        return False
    t_set, t_num, t_name = (truth.get(k, "").strip() for k in ("set", "number", "name"))
    if t_set and t_num:
        return (r.card.set_code.lower() == t_set.lower()
                and normalize_number(r.card.collector_number) == normalize_number(t_num))
    return bool(t_name) and compact(r.card.name) == compact(t_name)


def cmd_batch(args) -> int:
    v = _module(args, camera=_NoCamera())
    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not files:
        print(f"no images in {args.folder}")
        return 1
    truth = _load_truth(args.truth) if args.truth else {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, times, correct, judged = [], [], 0, 0
    for n, path in enumerate(files, 1):
        image = cv2.imread(str(path))
        if image is None:
            print(f"[{n}/{len(files)}] {path.name}: cannot read image")
            continue
        r = v.recognize(image)
        times.append(r.elapsed_ms)
        verdict = ""
        if path.name in truth:
            judged += 1
            ok = _is_correct(r, truth[path.name])
            correct += ok
            verdict = "OK" if ok else "WRONG"
        write_report(out, str(path), r, {"verdict": verdict} if verdict else None)
        card = f"{r.card.name} [{r.card.set_code} {r.card.collector_number}]" if r.card else "-"
        print(f"[{n}/{len(files)}] {path.name}: {r.status.value:14s} {r.confidence:3d}% "
              f"{(r.method.value if r.method else '-'):24s} {card}  ({r.elapsed_ms} ms) {verdict}")
        rows.append({
            "file": path.name, "status": r.status.value, "confidence": r.confidence,
            "method": r.method.value if r.method else "", "layout": r.layout.value if r.layout else "",
            "card_name": r.card.name if r.card else "", "set": r.card.set_code if r.card else "",
            "number": r.card.collector_number if r.card else "",
            "color_identity": r.card.color_identity_str if r.card else "",
            "print_id": r.card.print_id if r.card else "",
            "ocr_name": r.fields.name if r.fields else "", "ocr_type": r.fields.type if r.fields else "",
            "ocr_info": r.fields.info_raw if r.fields else "",
            "ocr_set": r.info.set_code if r.info else "", "ocr_number": r.info.collector_number if r.info else "",
            "elapsed_ms": r.elapsed_ms, "verdict": verdict,
        })

    with open(out / "resultados.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    by_status = {s.value: sum(r["status"] == s.value for r in rows) for s in IdentificationStatus}
    print("\n=======================================")
    print(f"Imagens: {total}")
    for s, k in by_status.items():
        print(f"  {s:15s} {k:4d}  ({100 * k / total:.1f}%)")
    print(f"Tempo por captura: média {statistics.mean(times):.0f} ms | mediana {statistics.median(times):.0f} ms"
          f" | máx {max(times)} ms")
    if judged:
        print(f"Acurácia (RNF18) sobre {judged} imagens com gabarito: {100 * correct / judged:.1f}%")
    print(f"Relatórios em: {out.resolve()}")
    print("=======================================")
    return 0


def cmd_camera(args) -> int:
    v = _module(args)
    try:
        out = Path(args.out)
        for k in range(args.n):
            frame = v.camera.capture()
            r = v.recognize(frame)
            name = f"captura_{frame.captured_at:%Y%m%d_%H%M%S}_{k}"
            out.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out / f"{name}.jpg"), frame.image)
            write_report(out, f"{name}.jpg", r)
            print(json.dumps(r.to_dict(), ensure_ascii=False))
    finally:
        v.close()
    return 0


class _NoCamera:
    """Câmara nula para comandos que trabalham com arquivos."""

    def configure(self):
        pass

    def capture(self):
        raise RuntimeError("no camera in this mode")

    def close(self):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cardforge.cv", description="CardForge-M1 vision tools")
    parser.add_argument("--config", default=None, help="vision.toml (padrão: ./vision.toml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="cria/atualiza o cards.db a partir do Scryfall")
    p.add_argument("--force", action="store_true", help="reconstrói mesmo que já esteja atualizado")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("identify", help="identifica imagens e imprime o resultado em JSON")
    p.add_argument("images", nargs="+")
    p.set_defaults(func=cmd_identify)

    p = sub.add_parser("batch", help="processa uma pasta e gera relatórios (+ acurácia com --truth)")
    p.add_argument("folder", nargs="?", default="input_cartas")
    p.add_argument("--out", default="relatorios")
    p.add_argument("--truth", help="CSV de gabarito: file,set,number,name")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("camera", help="(Raspberry Pi) captura com a câmara e identifica")
    p.add_argument("-n", type=int, default=1, help="número de capturas")
    p.add_argument("--out", default="capturas")
    p.set_defaults(func=cmd_camera)

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # nomes de cartas/artistas têm caracteres fora do cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
