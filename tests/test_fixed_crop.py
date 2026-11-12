"""Fixed crop: the card is cut from calibrated corners instead of being searched for in every picture."""

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from cardforge.cv.config import CropConfig, VisionConfig
from cardforge.cv.detection import CardDetector, ContourStrategy, fixed_quad, normalized_corners, order_points
from sorter_api.api import create_app
from sorter_api.identifier import VisionIdentifier
from test_integration import fast_settings

ROOT = Path(__file__).resolve().parents[1]
FOREST = ROOT / "input_cartas" / "20260925_230815.jpg"
needs_photos = pytest.mark.skipif(not (ROOT / "data/cards.db").exists() or not FOREST.exists(),
                                  reason="needs data/cards.db and input_cartas")


def test_fixed_quad_scales_the_saved_corners_to_the_frame():
    crop = CropConfig(mode="fixed", corners=[[0.25, 0.2], [0.5, 0.2], [0.5, 0.8], [0.25, 0.8]])  # card order
    quad = fixed_quad(crop, (1232, 1640))
    assert quad.strategy is ContourStrategy.FIXED
    assert np.allclose(quad.corners, [[410, 246.4], [820, 246.4], [820, 985.6], [410, 985.6]])
    assert normalized_corners(quad, (1232, 1640)) == crop.corners
    wider = fixed_quad(CropConfig(mode="fixed", corners=crop.corners, margin=0.1), (1232, 1640))
    assert np.allclose(wider.corners[0], [389.5, 209.44])                  # 10 % larger around the centre
    assert fixed_quad(CropConfig(mode="fixed"), (1232, 1640)) is None      # not calibrated


def test_vision_toml_rejects_an_unknown_mode(tmp_path):
    p = tmp_path / "vision.toml"
    p.write_text('[crop]\nmode = "auto"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        VisionConfig.load(p)


def test_start_is_blocked_until_the_crop_is_calibrated():
    class Vision:
        ready = True
        cfg = VisionConfig()
    Vision.cfg.crop = CropConfig(mode="fixed")
    assert [p[0] for p in VisionIdentifier(Vision()).problems()] == ["CROP_NOT_CALIBRATED"]
    Vision.cfg.crop.corners = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert VisionIdentifier(Vision()).problems() == []


@needs_photos
def test_identification_with_the_fixed_crop_skips_border_detection(monkeypatch):
    from cardforge.cv import VisionModule
    from cardforge.cv.capture import FileCameraService

    image = cv2.imread(str(FOREST))
    detected = CardDetector(VisionConfig()).detect(image)
    cfg = VisionConfig.load(ROOT / "vision.toml")
    cfg.db_path = str(ROOT / "data/cards.db")
    cfg.crop = CropConfig(mode="fixed", corners=normalized_corners(detected, image.shape))
    v = VisionModule(cfg, camera=FileCameraService([FOREST]))
    v.start(open_camera=False)

    def no_detection(*a, **k):
        raise AssertionError("border detection must not run with the fixed crop")
    monkeypatch.setattr(CardDetector, "detect", no_detection)
    r = v.recognize(image)
    assert r.identified and r.card.name == "Forest" and r.debug["strategy"] == "FIXED"

    cfg.crop.corners = []
    r = v.recognize(image)
    assert r.status.value == "CROP_FAILED" and r.debug["crop"] == "crop not calibrated"


@needs_photos
def test_calibration_page_measures_and_saves_the_crop(tmp_path):
    images = tmp_path / "imgs"
    images.mkdir()
    shutil.copy(FOREST, images / "0.jpg")
    toml = tmp_path / "vision.toml"
    toml.write_text((ROOT / "vision.toml").read_text(encoding="utf-8").replace(
        'db_path = "data/cards.db"', f'db_path = "{(ROOT / "data/cards.db").as_posix()}"'), encoding="utf-8")
    settings = fast_settings(vision="files", vision_images=str(images), vision_config=str(toml))
    with TestClient(create_app(settings)) as c:
        assert c.post("/calibration/crop/save").json()["code"] == "NO_SAMPLES"
        c.post("/calibration/feed")
        for _ in range(3):
            body = c.post("/calibration/crop/sample").json()
        assert body["crop"]["samples"] == 3 and body["crop"]["spread_px"] == 0.0
        saved = c.post("/calibration/crop/save").json()["crop"]
        assert saved["mode"] == "fixed" and saved["calibrated"]

        crop = VisionConfig.load(toml).crop
        assert crop.mode == "fixed" and np.allclose(crop.corners, saved["mean"], atol=1e-4)
        m = c.post("/calibration/snapshot").json()["metrics"]
        assert m["strategy"] == "FIXED" and m["crop"]["offset_px"] < 2       # detected card = saved crop
        ident = c.post("/calibration/identify").json()["result"]
        assert ident["card"]["name"] == "Forest" and ident["debug"]["strategy"] == "FIXED"
        corners = order_points(np.array(crop.corners))
        assert corners[0][0] < corners[1][0] and corners[1][1] < corners[2][1]  # TL, TR, BR, BL


@needs_photos
def test_crop_calibration_pictures_are_kept(tmp_path):
    import csv
    import json
    images = tmp_path / "imgs"
    images.mkdir()
    shutil.copy(FOREST, images / "0.jpg")
    toml = tmp_path / "vision.toml"
    toml.write_text((ROOT / "vision.toml").read_text(encoding="utf-8").replace(
        'db_path = "data/cards.db"', f'db_path = "{(ROOT / "data/cards.db").as_posix()}"'), encoding="utf-8")
    captures = tmp_path / "calibragem_recorte"
    settings = fast_settings(vision="files", vision_images=str(images), vision_config=str(toml),
                             crop_captures_dir=str(captures))
    with TestClient(create_app(settings)) as c:
        c.post("/calibration/feed")
        first = c.post("/calibration/crop/sample").json()["picture"]
        c.post("/calibration/crop/sample")
        c.post("/calibration/crop/save")
    rows = list(csv.DictReader(open(captures / "medicoes.csv", encoding="utf-8")))
    assert len(rows) == 2 and rows[0]["file"] == first and rows[0]["found"] == "1"
    assert len(json.loads(rows[0]["corners"])) == 4 and rows[0]["frame_width"] == str(cv2.imread(str(FOREST)).shape[1])
    assert (captures / first).exists() and (captures / first.replace(".jpg", "_anotada.jpg")).exists()
    assert cv2.imread(str(captures / first)).shape == cv2.imread(str(FOREST)).shape      # full resolution
    saved = list(captures.glob("recorte_salvo_*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8"))["samples"] == 2


@needs_photos
def test_a_picture_without_a_card_border_is_kept_too(tmp_path):
    blank = tmp_path / "imgs"
    blank.mkdir()
    cv2.imwrite(str(blank / "0.jpg"), np.full((600, 800, 3), 128, np.uint8))
    toml = tmp_path / "vision.toml"
    toml.write_text((ROOT / "vision.toml").read_text(encoding="utf-8").replace(
        'db_path = "data/cards.db"', f'db_path = "{(ROOT / "data/cards.db").as_posix()}"'), encoding="utf-8")
    captures = tmp_path / "cap"
    with TestClient(create_app(fast_settings(vision="files", vision_images=str(blank), vision_config=str(toml),
                                             crop_captures_dir=str(captures)))) as c:
        r = c.post("/calibration/crop/sample")
        assert r.status_code == 409 and "_sem_borda.jpg" in r.json()["message"]
    assert len(list(captures.glob("*_sem_borda.jpg"))) == 1


def test_a_card_lying_sideways_comes_out_upright():
    """The card in the reading chamber lies rotated 90° clockwise (title on the right edge)."""
    from cardforge.cv.detection import CardQuad, PerspectiveRectifier, to_card_order
    card = np.full((880, 630, 3), 200, np.uint8)
    card[20:90, 30:600] = (0, 0, 255)                                   # red title bar at the top
    lying = cv2.rotate(card, cv2.ROTATE_90_CLOCKWISE)                   # 630 x 880 -> 880 wide, 630 tall
    frame = np.zeros((1232, 1640, 3), np.uint8)
    frame[300:930, 400:1280] = lying
    image_order = np.float32([[400, 300], [1279, 300], [1279, 929], [400, 929]])
    corners = to_card_order(image_order)
    assert np.allclose(corners[0], [1279, 300])                         # card top-left = picture top-right
    out = PerspectiveRectifier(VisionConfig()).rectify(frame, CardQuad(corners, 0, ContourStrategy.FIXED)).image
    assert out.shape == (880, 630, 3) and out[50, 300, 2] > 200 and out[800, 300, 2] < 220


def test_outlier_measurements_are_left_out():
    from cardforge.cv.calibration import aggregate_crop_samples
    good = [[[0.80 + d, 0.34], [0.83, 0.94 + d], [0.19, 0.955], [0.19 + d, 0.37]] for d in (0, 0.005, -0.004, 0.003)]
    text_box_only = [[0.45, 0.37], [0.46, 0.94], [0.19, 0.95], [0.20, 0.38]]
    agg = aggregate_crop_samples(good + [text_box_only], (1232, 1640))
    assert agg["rejected"] == [4] and agg["used"] == [0, 1, 2, 3] and agg["spread_px"] < 15
    assert abs(agg["mean"][0][0] - 0.801) < 0.003


@needs_photos
def test_corners_marked_by_hand(tmp_path):
    images = tmp_path / "imgs"
    images.mkdir()
    shutil.copy(FOREST, images / "0.jpg")
    toml = tmp_path / "vision.toml"
    toml.write_text((ROOT / "vision.toml").read_text(encoding="utf-8").replace(
        'db_path = "data/cards.db"', f'db_path = "{(ROOT / "data/cards.db").as_posix()}"'), encoding="utf-8")
    image = cv2.imread(str(FOREST))
    corners = normalized_corners(CardDetector(VisionConfig()).detect(image), image.shape)
    settings = fast_settings(vision="files", vision_images=str(images), vision_config=str(toml),
                             crop_captures_dir=str(tmp_path / "cap"))
    with TestClient(create_app(settings)) as c:
        c.post("/calibration/feed")
        crossed = [corners[0], corners[2], corners[1], corners[3]]
        assert c.post("/calibration/crop/preview", json={"corners": crossed}).json()["code"] == "BAD_CORNERS"
        assert c.post("/calibration/crop/manual", json={"corners": corners[:3]}).status_code == 400
        preview = c.post("/calibration/crop/preview", json={"corners": corners}).json()
        assert preview["image"].startswith("data:image/jpeg") and preview["metrics"]["info_sharpness"] > 0
        saved = c.post("/calibration/crop/manual", json={"corners": corners}).json()
        assert saved["crop"]["calibrated"]
        ident = c.post("/calibration/identify").json()["result"]
        assert ident["card"]["name"] == "Forest" and ident["debug"]["strategy"] == "FIXED"
    assert np.allclose(VisionConfig.load(toml).crop.corners, corners, atol=1e-4)
    assert list((tmp_path / "cap").glob("recorte_salvo_*.json"))
