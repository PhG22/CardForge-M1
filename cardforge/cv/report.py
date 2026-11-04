from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .ocr.rois import RoiCatalog
from .result import IdentificationResult, IdentificationStatus

_STATUS_COLOR = {
    IdentificationStatus.IDENTIFIED: (0, 180, 0),
    IdentificationStatus.LOW_CONFIDENCE: (0, 165, 255),
    IdentificationStatus.CROP_FAILED: (0, 0, 255),
    IdentificationStatus.FACE_DOWN: (255, 0, 0),
}


def annotate(result: IdentificationResult, w: int = 630, h: int = 880) -> np.ndarray:
    """Carta retificada com as ROIs usadas (Nome verde, Tipo amarelo, Info magenta) e o status."""
    if result.rectified is None:
        img = np.zeros((h, w, 3), np.uint8)
        cv2.putText(img, result.status.value, (150, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        return img
    img = result.rectified.copy()
    if result.layout is not None:
        rois = RoiCatalog.rois_for(result.layout)
        for roi, color in ((rois.name, (0, 255, 0)), (rois.type, (0, 255, 255)), (rois.info, (255, 0, 255))):
            cv2.rectangle(img, *roi.rect(), color, 2)
    label = f"{result.status.value} {result.confidence}%"
    if result.layout is not None:
        label += f" {result.layout.value}"
    cv2.rectangle(img, (0, 0), (img.shape[1], 24), (0, 0, 0), -1)
    cv2.putText(img, label, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, _STATUS_COLOR[result.status], 2)
    return img


def text_report(source: str, r: IdentificationResult) -> str:
    f, i, c = r.fields, r.info, r.card
    lines = [
        "=======================================",
        "RELATÓRIO DE IDENTIFICAÇÃO — CardForge-M1",
        f"Arquivo: {source}",
        "=======================================",
        "",
        f"Status: {r.status.value}  |  Confiança: {r.confidence}%  |  Método: {r.method.value if r.method else 'N/A'}"
        + ("  |  REVISAR" if r.needs_review else ""),
        f"Layout: {r.layout.value if r.layout else 'N/A'}  |  Tempo: {r.elapsed_ms} ms"
        f"  |  Chamadas OCR: {r.debug.get('ocr_calls', '?')}",
        "",
        "--- LIDO PELA CÂMARA (OCR) ---",
        f"Nome: {f.name if f else 'N/A'}",
        f"Tipo: {f.type if f else 'N/A'}",
        f"Linha de info: {f.info_raw if f else 'N/A'}",
        f"Edição: {i.set_code if i else 'N/A'}  |  Número: {i.collector_number if i else 'N/A'}",
        "",
        "--- CARTA OFICIAL (cards.db / Scryfall) ---",
    ]
    if c:
        lines += [
            f"Nome: {c.name}",
            f"Custo de mana: {c.mana_cost or '-'}",
            f"Tipo: {c.type_line}",
            f"Identidade de cor: {c.color_identity_str or 'Incolor'}",
            f"Raridade: {c.rarity.capitalize()}",
            f"Edição / número: {c.set_code} {c.collector_number}",
            f"Scryfall id: {c.print_id}",
        ]
    else:
        lines.append("Nenhuma carta correspondente.")
    return "\n".join(lines) + "\n"


def write_report(out_dir: Path, source: str, r: IdentificationResult,
                 extra: Optional[dict] = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source).stem
    cv2.imwrite(str(out_dir / f"{stem}_processada.jpg"), annotate(r))
    (out_dir / f"{stem}_dados.txt").write_text(text_report(Path(source).name, r), encoding="utf-8")
    data = r.to_dict() | {"source": str(source)} | (extra or {})
    (out_dir / f"{stem}_dados.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
