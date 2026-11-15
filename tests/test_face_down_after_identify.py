"""
RF39 / RF29: a card back must be FACE_DOWN at the FIRST capture, never after all 3 of them.

Three checks, cheapest first:
  * colour alone, before any OCR (`brown_only`): the usual case, 0 OCR calls;
  * no title in either orientation (`no_title`);
  * colour again after a failed identification (`after_identify`): catches a back whose colour is
    below the shortcut threshold and whose "Deckmaster" logo the OCR read as a title.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cardforge.cv.config import VisionConfig  # noqa: E402
from cardforge.cv.detection.rectifier import RectifiedCard  # noqa: E402
from cardforge.cv.ocr import FaceDownDetector  # noqa: E402
from cardforge.cv.ocr.fields import ExtractedFields  # noqa: E402
from cardforge.cv.ocr.orientation import OrientationResult  # noqa: E402
from cardforge.cv.ocr.rois import CardLayout  # noqa: E402
from cardforge.cv.recognizer import CardRecognizer, _Attempt  # noqa: E402
from cardforge.cv.result import IdentificationStatus  # noqa: E402
from sorter_api.identifier import FACE_DOWN, UNIDENTIFIED, VisionIdentifier  # noqa: E402

BACK = np.full((880, 630, 3), (30, 80, 150), np.uint8)     # BGR brown/orange: the Magic card back
FRONT = np.full((880, 630, 3), (200, 200, 200), np.uint8)  # a light grey card front
# A back whose colour lands BETWEEN the two thresholds (0.75 <= ratio < 0.80): the colour-only
# shortcut must not fire, so it is caught by the check after the failed identification.
DIM_BACK = BACK.copy()
DIM_BACK[:int(0.27 * 880)] = (200, 200, 200)


def recognizer(card_image: np.ndarray, title: str = "Dkmstr") -> CardRecognizer:
    """A CardRecognizer whose camera crop, OCR and matching are stubs: OCR read `title`, no match."""
    cfg = VisionConfig()
    cfg.crop.mode = "fixed"
    cfg.crop.corners = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    cfg.skip_layout_when_identified = False
    r = CardRecognizer.__new__(CardRecognizer)
    r.cfg = cfg
    r.ocr = SimpleNamespace(calls=0)
    r.rectifier = SimpleNamespace(rectify=lambda image, quad: RectifiedCard(card_image))
    fields = ExtractedFields(name=title)
    # 3 characters read in each orientation: the early "no title" check does not fire
    r.orientation = SimpleNamespace(resolve=lambda card: OrientationResult(
        card, 0, fields.title_chars, fields, tried_rotation=True, rotated_title_chars=fields.title_chars))
    r.face_down = FaceDownDetector(cfg.face_down)
    r.layouts = SimpleNamespace(classify=lambda card: CardLayout.STANDARD)
    r._identify = lambda f, layout: _Attempt(f, None, layout, SimpleNamespace(card=None, method=None,
                                             confidence=0, needs_review=False, name_score=0), False)
    return r


def test_card_back_is_face_down_before_any_ocr():
    result = recognizer(BACK).recognize(np.zeros((720, 1280, 3), np.uint8))
    assert result.status is IdentificationStatus.FACE_DOWN
    assert result.debug["face_down_check"] == "brown_only"
    assert result.debug["ocr_calls"] == 0                 # nothing was read: ~1 ms instead of 2 OCR calls
    # The confidence is the colour signature itself, not 0 % (which read as a failure in the log).
    assert result.confidence == round(result.debug["brown_ratio"] * 100) >= 75


def test_card_back_with_ocr_noise_is_face_down_after_identification():
    cfg = VisionConfig()
    ratio = FaceDownDetector.brown_ratio(DIM_BACK)
    assert cfg.face_down.min_brown_ratio <= ratio < cfg.face_down.skip_ocr_brown_ratio
    result = recognizer(DIM_BACK).recognize(np.zeros((720, 1280, 3), np.uint8))
    assert result.status is IdentificationStatus.FACE_DOWN
    assert result.debug["face_down_check"] == "after_identify"
    assert result.debug["brown_ratio"] >= cfg.face_down.min_brown_ratio


def test_unidentified_front_still_asks_for_another_capture():
    result = recognizer(FRONT).recognize(np.zeros((720, 1280, 3), np.uint8))
    assert result.status is IdentificationStatus.LOW_CONFIDENCE
    assert not result.status.is_final and result.debug["brown_ratio"] < 0.75


def test_identifier_stops_at_the_first_capture_of_a_card_back():
    vision = SimpleNamespace(calls=0)
    rec = recognizer(BACK)

    def identify():
        vision.calls += 1
        return rec.recognize(np.zeros((720, 1280, 3), np.uint8))
    vision.identify = identify
    lights = []
    out = VisionIdentifier(vision, attempts=3, set_light=lights.append).identify(lambda s: None)
    assert (out.scan_status, out.tray, out.attempts, vision.calls) == (FACE_DOWN, 6, 1, 1)
    assert lights == []                              # no brighter retry capture (RNF09) was needed


def test_identifier_keeps_retrying_an_unidentified_front():
    vision = SimpleNamespace(calls=0)
    rec = recognizer(FRONT)

    def identify():
        vision.calls += 1
        return rec.recognize(np.zeros((720, 1280, 3), np.uint8))
    vision.identify = identify
    lights = []
    out = VisionIdentifier(vision, attempts=3, set_light=lights.append).identify(lambda s: None)
    assert (out.scan_status, out.tray, out.attempts, vision.calls) == (UNIDENTIFIED, 6, 3, 3)
    assert lights == [80, 100, 60]                   # +20 per retry, then back to the base level
