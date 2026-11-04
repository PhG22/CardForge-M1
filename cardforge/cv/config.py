from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


@dataclass
class CameraConfig:
    resolution: tuple[int, int] = (2028, 1520)   # OV5647: modo 2x2 binned, boa nitidez e rápido
    exposure_us: int = 20000                     # exposição fixa — a câmara fica numa câmara escura (RNF08)
    analogue_gain: float = 2.0
    awb_gains: tuple[float, float] = (1.6, 1.5)  # ganhos (vermelho, azul) fixos; 0 => AWB automático
    warmup_s: float = 0.5


@dataclass
class FaceDownConfig:
    """Assinatura do verso da carta de Magic: moldura e oval central marrons (verso ≈ 0,85; frentes ≤ 0,67)."""
    min_brown_ratio: float = 0.75
    max_text_chars: int = 2       # caracteres no título tolerados (ruído) para considerar "sem título"


@dataclass
class VisionConfig:
    """Limiares e dimensões centralizados (CV-CL-01). Pode ser sobrescrito por vision.toml."""
    db_path: str = "data/cards.db"
    work_height: int = 800
    min_area_ratio: float = 0.08
    max_area_ratio: float = 0.85                 # design: 0.60; nas fotos de teste a carta ocupa até 67 % do frame
    confidence_threshold: int = 90
    orientation_threshold: int = 75
    orientation_margin: int = 10
    name_cutoff: int = 85
    set_cutoff: int = 85
    cross_check_cutoff: int = 70
    card_size: tuple[int, int] = (630, 880)      # (largura, altura) da carta retificada
    aspect_range: tuple[float, float] = (1.20, 1.70)
    min_quad_rectangularity: float = 0.85        # área do quad / área do seu minAreaRect
    border_margin: float = 0.005                 # quads a menos de 0,5 % da borda do frame são rejeitados
    ocr_upscale: float = 1.5                     # 1.0 é mais rápido mas perdeu 4/63 cartas
    ocr_det_limit_type: str = "min"              # "min": amplia até o lado menor = side_len; "max": só reduz
    ocr_det_limit_side_len: int = 736            # ("max", 960) é ~20 % mais rápido, mas lê pior a linha de info
    ocr_angle_cls: bool = False                  # orientação 0°/180° já é resolvida pelo OrientationResolver
    # Pula o OCR de layout quando a leitura com as ROIs padrão já identifica a carta (nome e info ficam
    # no mesmo lugar em todos os layouts). false = sempre classifica o layout, como no design.
    skip_layout_when_identified: bool = True
    # Carta impressa noutro idioma (ex.: "Sagui Espinhoso"): se o nome lido não corresponde a nenhuma
    # carta inglesa, aceita set+número quando os tipos da linha de tipo batem ("Criatura" = Creature).
    # false = comportamento estrito do design CV-AD-04 (SET_NUMBER_NAME_MISMATCH -> needs_review).
    accept_localized_cards: bool = True
    camera: CameraConfig = field(default_factory=CameraConfig)
    face_down: FaceDownConfig = field(default_factory=FaceDownConfig)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "VisionConfig":
        """Carrega vision.toml (se existir). Chaves desconhecidas geram erro para evitar erros de digitação."""
        cfg = cls()
        if path is None:
            path = os.environ.get("CARDFORGE_VISION_CONFIG", "vision.toml")
        p = Path(path)
        if not p.exists():
            return cfg
        if tomllib is None:
            raise RuntimeError("vision.toml requires Python 3.11+ (tomllib)")
        with open(p, "rb") as f:
            data = tomllib.load(f)
        _apply(cfg, data, str(p))
        # Caminho relativo do banco é relativo ao vision.toml.
        if not Path(cfg.db_path).is_absolute():
            cfg.db_path = str((p.parent / cfg.db_path).resolve())
        return cfg


def _apply(obj, data: dict, where: str) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"{where}: unknown option '{key}'")
        current = getattr(obj, key)
        if isinstance(value, dict):
            _apply(current, value, f"{where}.{key}")
        elif isinstance(current, tuple):
            setattr(obj, key, tuple(value))
        else:
            setattr(obj, key, type(current)(value))
