from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np

from ..capture.camera import Frame
from ..config import VisionConfig
from .geometry import order_points


class ContourStrategy(str, Enum):
    CANNY = "CANNY"
    CANNY_FILL = "CANNY_FILL"
    OTSU_DARK = "OTSU_DARK"            # carta de borda escura sobre fundo claro
    OTSU_DARK_OPEN = "OTSU_DARK_OPEN"
    OTSU_LIGHT = "OTSU_LIGHT"          # carta de borda clara sobre fundo escuro


DARK_SET = (ContourStrategy.CANNY, ContourStrategy.CANNY_FILL,
            ContourStrategy.OTSU_DARK, ContourStrategy.OTSU_DARK_OPEN)
LIGHT_SET = (ContourStrategy.OTSU_LIGHT,)

# Passes (estratégias, retangularidade mínima para aceitar minAreaRect) — CV-AD-02.
PASSES = ((DARK_SET, 0.90), (LIGHT_SET, 0.90), (DARK_SET, 0.80))


@dataclass
class CardQuad:
    corners: np.ndarray        # (4, 2) float32, na resolução da imagem onde foi encontrado
    area: float
    strategy: ContourStrategy

    def scaled(self, ratio: float) -> "CardQuad":
        return CardQuad(self.corners * ratio, self.area * ratio * ratio, self.strategy)


def auto_canny(image: np.ndarray, sigma: float = 0.33) -> np.ndarray:
    """Canny com limiares derivados da mediana da imagem (mediana × (1 ± sigma))."""
    v = float(np.median(image))
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(image, lower, upper)


def _contours(mask: np.ndarray) -> list[np.ndarray]:
    found, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return list(found)


class ContourCandidateGenerator:
    """Gera contornos candidatos com várias estratégias de máscara (canny, preenchimento, Otsu)."""

    def __init__(self):
        self._rect5 = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        self._rect15 = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        self._ellipse31 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))

    def generate(self, gray_blur: np.ndarray) -> dict[ContourStrategy, list[np.ndarray]]:
        out: dict[ContourStrategy, list[np.ndarray]] = {}

        canny = cv2.morphologyEx(auto_canny(gray_blur), cv2.MORPH_CLOSE, self._rect5)
        out[ContourStrategy.CANNY] = _contours(canny)

        # Preenche os contornos externos e "abre" para remover ligações finas ao fundo.
        filled = np.zeros_like(canny)
        external, _ = cv2.findContours(canny, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(filled, external, -1, 255, thickness=cv2.FILLED)
        out[ContourStrategy.CANNY_FILL] = _contours(cv2.morphologyEx(filled, cv2.MORPH_OPEN, self._ellipse31))

        _, dark = cv2.threshold(gray_blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, self._rect15)
        out[ContourStrategy.OTSU_DARK] = _contours(dark)
        out[ContourStrategy.OTSU_DARK_OPEN] = _contours(cv2.morphologyEx(dark, cv2.MORPH_OPEN, self._ellipse31))

        _, light = cv2.threshold(gray_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out[ContourStrategy.OTSU_LIGHT] = _contours(cv2.morphologyEx(light, cv2.MORPH_CLOSE, self._rect15))
        return out


class QuadrilateralFinder:
    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg

    def has_card_aspect(self, pts: np.ndarray) -> bool:
        # Média dos lados opostos: mais estável que o minAreaRect quando há perspectiva (trapézio).
        p = order_points(pts)
        sides = [float(np.linalg.norm(p[i] - p[(i + 1) % 4])) for i in range(4)]
        a, b = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
        if min(a, b) <= 0:
            return False
        lo, hi = self.cfg.aspect_range
        return lo <= max(a, b) / min(a, b) <= hi

    def is_card_like(self, pts: np.ndarray, img_shape: tuple[int, int]) -> bool:
        """
        Proporção de carta, quase retangular (perspectiva leve) e sem encostar na borda do frame —
        evita quadriláteros "puxados" até o canto da imagem por sombras ou vinheta.
        """
        if not self.has_card_aspect(pts):
            return False
        rect = cv2.minAreaRect(pts.astype(np.float32))
        rect_area = rect[1][0] * rect[1][1]
        if rect_area <= 0 or cv2.contourArea(pts.astype(np.float32)) / rect_area < self.cfg.min_quad_rectangularity:
            return False
        h, w = img_shape
        m = self.cfg.border_margin * max(h, w)
        xs, ys = pts[:, 0], pts[:, 1]
        return xs.min() > m and ys.min() > m and xs.max() < w - 1 - m and ys.max() < h - 1 - m

    def find_card_quad(self, contours: list[np.ndarray], img_shape: tuple[int, int], rectangularity: float,
                       strategy: ContourStrategy) -> Optional[CardQuad]:
        img_area = float(img_shape[0] * img_shape[1])
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:15]:
            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            # Pequenos demais = ruído; grandes demais = moldura do frame/fundo, não a carta.
            if not (self.cfg.min_area_ratio * img_area <= hull_area <= self.cfg.max_area_ratio * img_area):
                continue
            peri = cv2.arcLength(hull, True)
            for eps in np.linspace(0.01, 0.10, 10):
                approx = cv2.approxPolyDP(hull, eps * peri, True)
                if len(approx) == 4:
                    pts = approx.reshape(4, 2).astype(np.float32)
                    if self.is_card_like(pts, img_shape):
                        return CardQuad(pts, float(cv2.contourArea(pts)), strategy)
            # Cantos arredondados: aceita o retângulo mínimo se o casco o preenche bem.
            rect = cv2.minAreaRect(hull)
            box = cv2.boxPoints(rect).astype(np.float32)
            box_area = rect[1][0] * rect[1][1]
            if box_area > 0 and hull_area / box_area > rectangularity and self.is_card_like(box, img_shape):
                return CardQuad(box, float(box_area), strategy)
        return None


class CardDetector:
    """Localiza o contorno externo da carta no frame (CV-SD-02a, passo 1)."""

    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg
        self.generator = ContourCandidateGenerator()
        self.finder = QuadrilateralFinder(cfg)

    def detect(self, frame: Frame | np.ndarray) -> Optional[CardQuad]:
        image = frame.image if isinstance(frame, Frame) else frame
        ratio = image.shape[0] / float(self.cfg.work_height)
        small = cv2.resize(image, (int(round(image.shape[1] / ratio)), self.cfg.work_height),
                           interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        img_area = float(small.shape[0] * small.shape[1])

        candidates_by_strategy = self.generator.generate(gray)
        candidates: list[CardQuad] = []
        for strategies, rectangularity in PASSES:
            for strategy in strategies:
                quad = self.finder.find_card_quad(candidates_by_strategy[strategy], gray.shape[:2],
                                                  rectangularity, strategy)
                # Quadriláteros enormes costumam ser a moldura da câmara/fundo, não a carta.
                if quad is not None and quad.area <= img_area * self.cfg.max_area_ratio:
                    candidates.append(quad)
            if candidates:
                break
        if not candidates:
            return None
        return self.select_best(candidates).scaled(ratio)

    @staticmethod
    def select_best(candidates: list[CardQuad]) -> CardQuad:
        # O maior candidato válido é a borda externa (e não a caixa de arte ou de texto).
        return max(candidates, key=lambda q: q.area)
