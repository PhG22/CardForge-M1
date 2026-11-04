from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OcrLine:
    box: list[list[float]]   # 4 pontos (x, y)
    text: str
    score: float

    def y_center(self) -> float:
        return (self.box[0][1] + self.box[2][1]) / 2.0


class OcrEngine:
    """
    RapidOCR sobre ONNX Runtime — deteção → (classificador de ângulo) → reconhecimento, tudo local (RNF15).

    Medido nas 63 fotos da PoC:
    - classificador de ângulo desligado: −11 % de tempo, nenhuma perda (cartas de cabeça para baixo já são
      tratadas pelo OrientationResolver);
    - detector com limit_type "max" (sem ampliar as faixas): −20 % de tempo, mas 4 cartas a menos confirmadas
      por set+número (o texto pequeno da linha de info fica pior). Por isso o padrão mantém "min" 736.
    """

    def __init__(self, det_limit_type: str = "min", det_limit_side_len: int = 736, angle_cls: bool = False):
        from rapidocr_onnxruntime import RapidOCR
        # det_model_path=None é obrigatório quando se passa qualquer opção "det_*" (o RapidOCR usa o padrão).
        self.rapid_ocr = RapidOCR(det_model_path=None, det_limit_type=det_limit_type,
                                  det_limit_side_len=det_limit_side_len, use_angle_cls=angle_cls)
        self.calls = 0

    def read(self, img: np.ndarray) -> list[OcrLine]:
        self.calls += 1
        result, _ = self.rapid_ocr(img)
        if not result:
            return []
        return [OcrLine(box=r[0], text=str(r[1]), score=float(r[2])) for r in result]

    def read_text(self, img: np.ndarray) -> str:
        return " ".join(line.text for line in self.read(img)).strip()
