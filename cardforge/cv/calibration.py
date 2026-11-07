"""
Calibração da câmera com a máquina montada (página /calibration do serviço, ou VisionModule direto).

Mede, numa foto da carta parada na câmara de leitura:
* brilho e saturação do quadro (áreas estouradas / pretas) -> ajuste de exposição e ganho;
* se a carta foi encontrada, a estratégia e a área ocupada -> enquadramento e max/min_area_ratio;
* uniformidade da luz na carta (grade 3 × 3) e o equilíbrio de cor -> posição das fitas LED e branco;
* nitidez da carta inteira e da linha de informação (texto pequeno) -> foco (RF15).
Também grava os valores escolhidos na seção [camera] do vision.toml.
"""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

from .capture.camera import sharpness
from .config import VisionConfig
from .detection import CardDetector, PerspectiveRectifier
from .ocr.rois import CardLayout, RoiCatalog

# Referências medidas nas 63 fotos da PoC: cartas lidas por edição+número tinham brilho 64–133 e
# nitidez da linha de informação a partir de 77. Refinar com fotos da câmara real.
TARGET_BRIGHTNESS = (60, 180)       # média 0–255 da carta retificada
MAX_CLIPPED_PCT = 2.0               # % de pixels estourados (≥ 250) no quadro
MAX_UNEVENNESS_PCT = 20.0           # (máx − mín) / máx das 9 células da grade — só com alvo branco
MIN_INFO_SHARPNESS = 70.0           # variância do Laplaciano na linha de informação
NEUTRAL_MAX_SATURATION = 35         # alvo neutro (cartão branco/cinza liso): saturação média HSV abaixo disso


def frame_metrics(image: np.ndarray, cfg: VisionConfig) -> tuple[dict, np.ndarray]:
    """Métricas de uma foto + imagem anotada (quadrilátero da carta e ROIs) para a pré-visualização."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    m: dict = {
        "frame": {"width": w, "height": h,
                  "brightness": round(float(gray.mean()), 1),
                  "clipped_high_pct": round(float((gray >= 250).mean() * 100), 2),
                  "clipped_low_pct": round(float((gray <= 5).mean() * 100), 2)},
        "card_found": False,
    }
    overlay = image.copy()
    quad = CardDetector(cfg).detect(image)
    if quad is None:
        m["advice"] = ["Carta não encontrada: confira se há uma carta parada na plataforma, o enquadramento "
                       "e o contraste da borda com o fundo."]
        _label(overlay, "CARTA NAO ENCONTRADA", (0, 0, 255))
        return m, overlay

    card = PerspectiveRectifier(cfg).rectify(image, quad).image
    cgray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    ch, cw = cgray.shape
    cells = [float(cgray[i * ch // 3:(i + 1) * ch // 3, j * cw // 3:(j + 1) * cw // 3].mean())
             for i in range(3) for j in range(3)]
    info = RoiCatalog.rois_for(CardLayout.STANDARD).info.crop(card)
    b, g, r = (float(card[..., k].mean()) for k in range(3))
    saturation = float(cv2.cvtColor(card, cv2.COLOR_BGR2HSV)[..., 1].mean())
    area_ratio = quad.area / float(w * h)
    m.update({
        "card_found": True,
        "strategy": quad.strategy.value,
        "area_ratio": round(area_ratio, 3),
        "corners": [[round(float(x)), round(float(y))] for x, y in quad.corners],
        "card": {
            "brightness": round(float(cgray.mean()), 1),
            "grid": [round(c, 1) for c in cells],
            "unevenness_pct": round((max(cells) - min(cells)) / max(max(cells), 1.0) * 100, 1),
            "color_means_bgr": [round(b, 1), round(g, 1), round(r, 1)],
            # Uniformidade e branco só valem com um alvo liso; numa carta comum refletem a arte.
            "neutral_target": saturation < NEUTRAL_MAX_SATURATION,
            "sharpness": round(sharpness(card), 1),
            "info_sharpness": round(sharpness(info), 1),
        },
    })
    m["advice"] = _advice(m, cfg)

    cv2.polylines(overlay, [quad.corners.astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0),
                  max(2, w // 300))
    _label(overlay, f"area {area_ratio:.0%}  {quad.strategy.value}", (0, 200, 0))
    return m, overlay


def _advice(m: dict, cfg: VisionConfig) -> list[str]:
    tips, c = [], m["card"]
    lo, hi = TARGET_BRIGHTNESS
    if c["brightness"] < lo:
        tips.append(f"Carta escura ({c['brightness']:.0f}): aumente a exposição, o ganho ou a luz.")
    elif c["brightness"] > hi:
        tips.append(f"Carta clara demais ({c['brightness']:.0f}): diminua a exposição, o ganho ou a luz.")
    if m["frame"]["clipped_high_pct"] > MAX_CLIPPED_PCT:
        tips.append(f"{m['frame']['clipped_high_pct']:.1f} % do quadro estourado: reflexo das fitas LED "
                    "ou exposição alta demais.")
    if c["neutral_target"] and c["unevenness_pct"] > MAX_UNEVENNESS_PCT:
        tips.append(f"Luz desigual no alvo ({c['unevenness_pct']:.0f} % entre as células da grade): "
                    "reposicione ou difunda as fitas LED.")
    if c["info_sharpness"] < MIN_INFO_SHARPNESS:
        tips.append(f"Texto pequeno pouco nítido ({c['info_sharpness']:.0f}): confira o foco da lente "
                    "(ou se a carta está tremendo na foto).")
    ratio = m["area_ratio"]
    if ratio > cfg.max_area_ratio * 0.95:
        tips.append(f"A carta ocupa {ratio:.0%} do quadro, perto do limite max_area_ratio "
                    f"({cfg.max_area_ratio:.0%}): use max_area_ratio = {min(ratio * 1.15, 0.95):.2f}.")
    if ratio < cfg.min_area_ratio * 1.5:
        tips.append(f"A carta ocupa só {ratio:.0%} do quadro: aproxime a câmera ou reduza min_area_ratio.")
    b, g, r = c["color_means_bgr"]
    if c["neutral_target"] and max(b, g, r) - min(b, g, r) > 0.10 * max(b, g, r, 1.0):
        tips.append("O alvo branco saiu colorido: rode a autocalibração (com o alvo na plataforma) "
                    "ou ajuste os ganhos de branco.")
    if not c["neutral_target"]:
        tips.append("Para medir a uniformidade da luz e o balanço de branco, coloque um cartão branco liso "
                    "do tamanho de uma carta na plataforma: numa carta comum, a arte altera essas medidas.")
    return tips or ["Tudo dentro das referências."]


def _label(img: np.ndarray, text: str, color) -> None:
    scale = max(img.shape[1] / 1200, 0.6)
    cv2.rectangle(img, (0, 0), (img.shape[1], int(40 * scale)), (0, 0, 0), -1)
    cv2.putText(img, text, (10, int(28 * scale)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, max(1, int(2 * scale)))


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, float):
        return f"{v:.4g}" if "e" not in f"{v:.4g}" else repr(v)
    return str(v)


def save_section(path: str | Path, section: str, values: dict) -> None:
    """
    Atualiza (ou cria) chaves de uma seção do vision.toml, preservando o resto do arquivo e os
    comentários no fim das linhas. Não é um escritor TOML geral: só valores simples e listas.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    start = next((i for i, ln in enumerate(lines) if re.match(rf"\s*\[{re.escape(section)}\]", ln)), None)
    if start is None:
        lines += ["", f"[{section}]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"\s*\[", lines[i])), len(lines))
    pending = dict(values)
    for i in range(start + 1, end):
        match = re.match(r"(\s*)([A-Za-z_]+)(\s*=\s*)([^#]*?)(\s*#.*)?$", lines[i])
        if match and match.group(2) in pending:
            indent, key, eq, _, comment = match.groups()
            value = _toml_value(pending.pop(key))
            lines[i] = f"{indent}{key}{eq}{value}" + (f"  {comment.strip()}" if comment else "")
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1                     # antes das linhas em branco que separam a próxima seção
    for key, value in pending.items():
        lines.insert(insert_at, f"{key} = {_toml_value(value)}")
        insert_at += 1
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
