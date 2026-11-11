"""Camera calibration with the machine fully assembled, from a browser on the same network.

    GET  /calibration                  the calibration page (phone or laptop: http://<raspberry-ip>:8000/calibration)
    POST /calibration/snapshot         one capture: metrics + annotated JPEG (base64)
    GET  /calibration/frame.jpg        one capture as JPEG (?overlay=1 draws the detected card)
    GET  /calibration/settings         exposure, gain, white balance, light level
    PUT  /calibration/settings         change them live (not saved)
    POST /calibration/auto             automatic exposure + white balance with the card in place, then locked
    POST /calibration/save             write the current values to vision.toml ([camera])
    POST /calibration/reference        remember the card brightness, to compare ambient light vs. dark (RNF08)
    POST /calibration/identify         full identification of the card on the platform
    GET  /calibration/crop             fixed crop: mode, corners, measured samples
    POST /calibration/crop/sample      measure where the card on the platform is (border detection, calibration only);
                                       the picture is kept in calibragem_recorte/ (original, annotated, medicoes.csv)
    DELETE /calibration/crop/samples   forget the measurements
    POST /calibration/crop/save        the average position becomes the fixed crop (vision.toml [crop])
    POST /calibration/feed             feed one card onto the platform (A1)
    POST /calibration/drop             drop it into the overflow tray 6 (A2 + A1)

The service owns the camera, so rpicam-hello / rpicam-still cannot open it while it runs:
everything goes through here. All routes answer 409 while a run is active, and 409
VISION_OFF when the service runs with MTG_VISION=off.
"""

from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import cv2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .bus import BusError, ModuleNotResponding
from .controller import HardwareFault, ManualRefused, _describe_fault
from .housekeeping import PROJECT_ROOT, CropCaptureLog, project_path
from .protocol import FrameError

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("static") / "calibration.html"
PREVIEW_WIDTH = 960


class CameraSettings(BaseModel):
    exposure_us: Optional[int] = Field(None, ge=100, le=1_000_000)
    analogue_gain: Optional[float] = Field(None, ge=1.0, le=64.0)   # clamped to the sensor's own range
    awb_gains: Optional[List[float]] = Field(None, min_length=2, max_length=2)
    light_level: Optional[int] = Field(None, ge=0, le=100)


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def create_calibration_router(settings) -> APIRouter:
    router = APIRouter(prefix="/calibration", tags=["calibration"])

    def vision_of(request: Request):
        vision = getattr(request.app.state, "vision_module", None)
        if vision is None:
            raise Refused(409, "VISION_OFF", "The camera is not in use (MTG_VISION=off). "
                                             "Restart the service with MTG_VISION=camera.")
        request.app.state.controller.require_idle()
        return vision

    @contextmanager
    def still_card(vision):
        """With photos instead of a camera (MTG_VISION=files), keep showing the same 'card'."""
        cam = vision.camera
        previous = getattr(cam, "hold", None)
        if previous is not None:
            cam.hold = True
        try:
            yield
        finally:
            if previous is not None:
                cam.hold = previous

    def handle(fn):
        try:
            return fn()
        except Refused as exc:
            return JSONResponse(status_code=exc.status, content={"ok": False, "code": exc.code,
                                                                 "message": exc.message})
        except ManualRefused as exc:
            return JSONResponse(status_code=exc.http_status, content={"ok": False, "code": exc.code,
                                                                      "message": exc.message})
        except ModuleNotResponding as exc:
            return JSONResponse(status_code=503, content={"ok": False, "code": "MODULE_OFFLINE",
                                                          "message": f"Module {exc.addr} is not responding."})
        except (HardwareFault, BusError, FrameError) as exc:
            code, message = _describe_fault(exc)
            return JSONResponse(status_code=500, content={"ok": False, "code": code, "message": message})

    def jpeg(image, width=PREVIEW_WIDTH) -> bytes:
        h, w = image.shape[:2]
        if w > width:
            image = cv2.resize(image, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()

    def with_reference(request: Request, metrics: dict) -> dict:
        ref = getattr(request.app.state, "calibration_reference", None)
        if ref and metrics.get("card_found"):
            now = metrics["card"]["brightness"]
            metrics["reference"] = {"brightness": ref, "delta_pct": round(abs(now - ref) / max(ref, 1.0) * 100, 1),
                                    "limit_pct": 10.0}
        return metrics

    def apply_light(request: Request, vision, level: int) -> None:
        vision.cfg.camera.light_level = level
        settings.light_base_level = level
        identifier = request.app.state.controller.identifier
        if hasattr(identifier, "light_base"):
            identifier.light_base = level
        request.app.state.controller.set_chamber_light(level)

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    def page():
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @router.post("/snapshot")
    def snapshot(request: Request):
        def run():
            vision = vision_of(request)
            with still_card(vision):
                frame, metrics, overlay = vision.calibration_snapshot()
            return {"ok": True, "metrics": with_reference(request, metrics), "settings": vision.camera_settings(),
                    "image": "data:image/jpeg;base64," + base64.b64encode(jpeg(overlay)).decode()}
        return handle(run)

    @router.get("/frame.jpg")
    def frame_jpg(request: Request, overlay: bool = True, width: int = PREVIEW_WIDTH):
        def run():
            vision = vision_of(request)
            with still_card(vision):
                frame, _, annotated = vision.calibration_snapshot()
            image = annotated if overlay else frame.image
            return Response(jpeg(image, max(160, min(width, 4096))), media_type="image/jpeg")
        return handle(run)

    @router.get("/settings")
    def get_settings(request: Request):
        return handle(lambda: {"ok": True, "settings": vision_of(request).camera_settings()})

    @router.put("/settings")
    def put_settings(body: CameraSettings, request: Request):
        def run():
            vision = vision_of(request)
            vision.tune_camera(body.exposure_us, body.analogue_gain,
                               tuple(body.awb_gains) if body.awb_gains else None)
            if body.light_level is not None:
                apply_light(request, vision, body.light_level)
            return {"ok": True, "settings": vision.camera_settings()}
        return handle(run)

    @router.post("/auto")
    def auto(request: Request):
        def run():
            vision = vision_of(request)
            with still_card(vision):
                values = vision.auto_tune_camera()
            return {"ok": True, "settings": values,
                    "message": "Exposure and white balance found and locked. Save to keep them."}
        return handle(run)

    @router.post("/save")
    def save(request: Request):
        def run():
            path = vision_of(request).save_camera_settings()
            return {"ok": True, "message": f"Saved in {path}.", "path": path}
        return handle(run)

    @router.post("/reference")
    def reference(request: Request):
        def run():
            vision = vision_of(request)
            with still_card(vision):
                _, metrics, _ = vision.calibration_snapshot()
            if not metrics["card_found"]:
                raise Refused(409, "NO_CARD", "No card found in the picture.")
            request.app.state.calibration_reference = metrics["card"]["brightness"]
            return {"ok": True, "brightness": metrics["card"]["brightness"],
                    "message": "Reference saved: now change the room light and take new pictures."}
        return handle(run)

    @router.post("/identify")
    def identify(request: Request):
        def run():
            vision = vision_of(request)
            with still_card(vision):
                result = vision.identify()
            from cardforge.cv.report import annotate

            return {"ok": True, "result": result.to_dict(),
                    "image": "data:image/jpeg;base64," + base64.b64encode(jpeg(annotate(result), 630)).decode()}
        return handle(run)

    # ---- fixed crop: measure where the card stops (a few cards), save the average as the crop

    def crop_log(request: Request) -> CropCaptureLog:
        root = Path(settings.project_root) if settings.project_root else PROJECT_ROOT
        return CropCaptureLog(project_path(settings.crop_captures_dir, root))

    def crop_summary(request: Request, size=None) -> dict:
        import numpy as np

        vision = request.app.state.vision_module
        samples = request.app.state.crop_samples
        out = {"mode": vision.cfg.crop.mode, "calibrated": len(vision.cfg.crop.corners) == 4,
               "corners": vision.cfg.crop.corners, "samples": len(samples)}
        if samples:
            pts = np.array(samples, dtype=np.float64)                        # (n, 4, 2), fractions
            mean = pts.mean(axis=0)
            h, w = size or vision.cfg.camera.resolution[::-1]
            spread = np.abs(pts - mean) * np.array([w, h])                   # px at the camera resolution
            out.update(mean=[[round(float(x), 5), round(float(y), 5)] for x, y in mean],
                       spread_px=round(float(np.linalg.norm(spread, axis=2).max()), 1))
        return out

    @router.get("/crop")
    def crop_status(request: Request):
        def run():
            vision_of(request)
            return {"ok": True, "crop": crop_summary(request)}
        return handle(run)

    @router.post("/crop/sample")
    def crop_sample(request: Request):
        """Find the card on the platform (border detection, calibration only) and keep its corners."""
        def run():
            vision = vision_of(request)
            with still_card(vision):
                corners, image, quad = vision.measure_crop()
            # Every picture is kept (also the failed ones) to debug the crop later.
            saved = crop_log(request).save_sample(image, corners, quad.strategy.value if quad else None,
                                                  quad.corners if quad else None)
            if corners is None:
                raise Refused(409, "NO_CARD", "The card border was not found in this picture: check the light "
                                              f"and that a card is on the platform. Picture saved as {saved}.")
            request.app.state.crop_samples.append(corners)
            return {"ok": True, "crop": crop_summary(request, image.shape[:2]), "picture": saved,
                    "message": f"Card position measured ({len(request.app.state.crop_samples)} so far), "
                               f"picture saved as {saved}."}
        return handle(run)

    @router.delete("/crop/samples")
    def crop_reset(request: Request):
        def run():
            vision_of(request)
            request.app.state.crop_samples.clear()
            return {"ok": True, "crop": crop_summary(request)}
        return handle(run)

    @router.post("/crop/save")
    def crop_save(request: Request):
        """The average of the measured positions becomes the fixed crop (now and in vision.toml)."""
        def run():
            vision = vision_of(request)
            summary = crop_summary(request)
            if not summary["samples"]:
                raise Refused(409, "NO_SAMPLES", "Measure the position of at least one card first.")
            path = vision.save_crop(summary["mean"])
            crop_log(request).save_result(summary, path)
            return {"ok": True, "crop": crop_summary(request),
                    "message": f"Fixed crop saved in {path}: cards are no longer searched for in the picture."}
        return handle(run)

    @router.post("/feed")
    def feed(request: Request):
        def run():
            vision_of(request)
            result = request.app.state.controller.calibration_feed()
            advance = getattr(request.app.state.vision_module.camera, "advance", None)
            if advance and result["platform_has_card"]:
                advance()
            return {"ok": True, **result}
        return handle(run)

    @router.post("/drop")
    def drop(request: Request):
        def run():
            vision_of(request)
            return {"ok": True, **request.app.state.controller.calibration_drop()}
        return handle(run)

    return router
