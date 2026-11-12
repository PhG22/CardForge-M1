from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


# Valores por sensor, antes do que o vision.toml define em [camera.profiles.<sensor>].
# Resolução cheia nos dois: com a carta ocupando ~2/3 do quadro em 1640x1232, a linha de edição/número
# do rodapé ficou com ~8 px de altura e ilegível para o OCR (nitidez 33 contra 289 nas fotos da PoC).
SENSOR_PROFILES: dict[str, dict] = {
    "imx219": {"resolution": (3280, 2464)},      # Camera Module v2, 8 MP
    "ov5647": {"resolution": (2592, 1944)},      # Camera Module v1, 5 MP
}
PROFILE_KEYS = ("resolution", "exposure_us", "analogue_gain", "awb_gains")


@dataclass
class CameraConfig:
    backend: str = "picamera2"                   # "picamera2" (padrão) ou "rpicam" (chama rpicam-still)
    # "auto" = usa o perfil do sensor detectado (imx219, ov5647...). Um nome fixo = avisa no log se a
    # câmera conectada for outra (o perfil usado continua sendo o do sensor detectado).
    sensor: str = "auto"
    # Os 4 valores abaixo são os efetivos: vêm do perfil do sensor detectado ([camera.profiles.<sensor>]);
    # os escritos direto em [camera] só valem quando o sensor não é conhecido (ex.: fotos de arquivo).
    resolution: tuple[int, int] = (3280, 2464)
    exposure_us: int = 20000                     # exposição fixa — a câmara fica numa câmara escura (RNF08)
    analogue_gain: float = 2.0
    awb_gains: tuple[float, float] = (1.6, 1.5)  # ganhos (vermelho, azul) fixos; 0 => AWB automático
    warmup_s: float = 0.5
    light_level: int = 60                        # fitas LED da câmara, % (nível base; RNF09 soma degraus)
    tmp_dir: str = "data/tmp"                    # arquivos temporários (backend rpicam); limpo ao fim de cada run
    profiles: dict = field(default_factory=dict) # [camera.profiles.<sensor>]: resolution, exposure_us, ...

    def apply_profile(self, sensor: str) -> None:
        """Valores do sensor: padrões do código <- SENSOR_PROFILES <- [camera.profiles.<sensor>]."""
        defaults = CameraConfig()
        values = {k: getattr(defaults, k) for k in PROFILE_KEYS}
        values.update(SENSOR_PROFILES.get(sensor, {}))
        values.update(self.profiles.get(sensor, {}))
        for key, value in values.items():
            if key not in PROFILE_KEYS:
                raise ValueError(f"vision.toml [camera.profiles.{sensor}]: unknown option '{key}'")
            setattr(self, key, tuple(value) if isinstance(getattr(defaults, key), tuple) else
                    type(getattr(defaults, key))(value))


@dataclass
class FaceDownConfig:
    """Assinatura do verso da carta de Magic: moldura e oval central marrons (verso ≈ 0,85; frentes ≤ 0,67)."""
    min_brown_ratio: float = 0.75
    max_text_chars: int = 2       # caracteres no título tolerados (ruído) para considerar "sem título"


@dataclass
class CropConfig:
    """
    Onde está a carta na foto.
    * "fixed": 4 cantos calibrados uma vez (a carta para sempre no mesmo lugar da câmara de leitura).
      Sem detecção de bordas a cada foto: mais rápido e sem confundir a borda da carta com o fundo.
    * "detect": detecção de bordas a cada foto (fotos de outras fontes, como as da prova de conceito).
    """
    mode: str = "detect"                         # o vision.toml da máquina usa "fixed"
    # [[x, y] x 4] em fração do quadro (0–1), na ORDEM DA CARTA: canto superior esquerdo da carta (lado do
    # título), superior direito, inferior direito, inferior esquerdo — vale para a carta deitada ou girada.
    corners: list = field(default_factory=list)
    # Recorte por câmera ([crop.corners_by_sensor] imx219 = [...]): cada módulo enquadra a carta de um jeito.
    # Quando o sensor detectado tem entrada aqui, ela substitui `corners`.
    corners_by_sensor: dict = field(default_factory=dict)
    margin: float = 0.0                          # amplia o recorte em volta da carta (0.02 = 2 %)


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
    crop: CropConfig = field(default_factory=CropConfig)
    face_down: FaceDownConfig = field(default_factory=FaceDownConfig)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "VisionConfig":
        """Carrega vision.toml (se existir). Chaves desconhecidas geram erro para evitar erros de digitação."""
        cfg = cls()
        if path is None:
            path = os.environ.get("CARDFORGE_VISION_CONFIG", "vision.toml")
        p = Path(path)
        cfg.source_path = str(p)                 # a calibração grava de volta neste arquivo
        if not p.exists():
            return cfg
        if tomllib is None:
            raise RuntimeError("vision.toml requires Python 3.11+ (tomllib)")
        with open(p, "rb") as f:
            data = tomllib.load(f)
        _apply(cfg, data, str(p))
        if cfg.crop.mode not in ("fixed", "detect"):
            raise ValueError(f"{p}: [crop] mode must be \"fixed\" or \"detect\"")
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
        if isinstance(current, dict):            # tabelas livres: perfis e recortes por sensor
            setattr(obj, key, {str(k): (dict(v) if isinstance(v, dict) else v) for k, v in value.items()})
        elif isinstance(value, dict):
            _apply(current, value, f"{where}.{key}")
        elif isinstance(current, tuple):
            setattr(obj, key, tuple(value))
        else:
            setattr(obj, key, type(current)(value))
