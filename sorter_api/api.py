"""REST + WebSocket API served by the Raspberry Pi.

    POST /run/start       prepare the machine, check conditions, start sorting
    POST /run/stop        finish the card in progress, close the flap, stop the motors
                          (?immediate=true: stop the motors right now)
    POST /run/resume      continue a run paused by a fault (re-homes the base first)
    GET  /run/status      current state + current/last run
    GET  /runs            past runs (run.db)
    GET  /runs/{id}       results of a finished run: per-bin counts + every card (RF22)
    GET  /inventory/export.csv   every sorted card, RFC 4180 CSV (RF24)
    GET  /stack           does the stack IR see cards?
    GET  /hardware/status live STATUS of A1 and A2 (debugging)
    GET  /flap, POST /flap, PUT /flap/angles   flap test: move it to any angle, tune the
                          open/closed angles the runs use (live, until restart)
    GET  /health          liveness check
    GET  /sync/status     upload of finished runs to the CardForge server (sync.py)
    POST /sync/now        try the uploads now instead of waiting for the next retry
    WS   /ws/events       run_started / card_sorted / attention / fault / run_finished notifications

    With MTG_SERVER_URL (a CardForge server, docs/SERVER.md): /run/start needs the app
    user's token (Authorization: Bearer ...), and finished runs are uploaded to the server.
    Without it, the Pi answers the app's server routes itself (app_compat.py):
    /auth/login, /cards, /machines/{id}/...
    + camera calibration with the machine assembled (calibration_api.py): /calibration

Run with:  python -m sorter_api            (settings from MTG_* env variables)
     or:   uvicorn --factory sorter_api.api:create_app --host 0.0.0.0
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .bus import BusError, GpioDirectionPin, RS485Bus, RtsDirectionPin, SerialPort
from .config import Settings
from .app_compat import create_app_router
from .calibration_api import create_calibration_router
from .controller import (HardwareFault, ManualRefused, NoActiveRun, NotPaused, SorterController, StartRefused,
                         _describe_fault)
from .bus import ModuleError, ModuleNotResponding
from .events import EventHub
from .identifier import PlaceholderIdentifier, VisionIdentifier
from .modules import BottomModule, TopModule
from .protocol import FrameError
from .sim import SimSerialPort, SimWorld
from .status_led import NetworkMonitor, build_status_led
from .store import RunStore
from .sync import AlertForwarder, NotSignedIn, ServerClient, ServerError, ServerUnreachable, SyncWorker

log = logging.getLogger(__name__)


def open_serial(settings: Settings) -> SerialPort:
    import serial  # pyserial

    return serial.Serial(
        settings.serial_port,
        baudrate=settings.baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.02,          # short reads; the bus enforces the real reply timeout
        write_timeout=1.0,
    )


class BaseMove(BaseModel):
    steps: Optional[int] = Field(None, description="Absolute position in steps from home (+ = clockwise)")
    degrees: Optional[float] = Field(None, description="Absolute position in degrees from home (+ = clockwise)")


class FlapMove(BaseModel):
    angle: Optional[int] = Field(None, description="Any angle 0-180 (degrees)")
    position: Optional[str] = Field(None, description='"open" or "closed": the angles the runs use')


class FlapAngles(BaseModel):
    open_angle: Optional[int] = Field(None, description="Angle the runs open the flap to (drop)")
    closed_angle: Optional[int] = Field(None, description="Angle the runs close the flap to")


class SimStateUpdate(BaseModel):
    # typing.Optional/List (not `int | None`): pydantic evaluates these at
    # runtime, and the `|` syntax needs Python 3.10+.
    stack_cards: Optional[int] = Field(None, ge=0, description="Cards on the simulated stack")
    trays_with_cards: Optional[List[int]] = Field(None, description="Trays (1-6) that are not empty")
    platform_has_card: Optional[bool] = None
    top_jammed: Optional[bool] = Field(None, description="Feeder never delivers a card")
    offline_modules: Optional[List[str]] = Field(None, description='e.g. ["A2"] to unplug the bottom module')


def build_identifier(settings: Settings, vision=None):
    """The card identifier for MTG_VISION (see config.py). Returns (identifier, vision module or None)."""
    if vision is None and settings.vision == "off":
        return PlaceholderIdentifier(settings.identify_delay_s, settings.trays), None
    if vision is None:
        from pathlib import Path

        from cardforge.cv import DatabaseUnavailable, VisionConfig, VisionModule
        from cardforge.cv.capture import FileCameraService

        cfg = VisionConfig.load(settings.vision_config)
        if settings.vision == "files":
            images = sorted(p for p in Path(settings.vision_images).glob("*")
                            if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
            if not images:
                raise RuntimeError(f"MTG_VISION=files but {settings.vision_images} has no images")
            # Photos from other cameras/positions: the card is found by border detection, not the fixed crop.
            cfg.crop.mode = "detect"
            vision = VisionModule(cfg, camera=FileCameraService(images, cfg.camera))
        elif settings.vision == "camera":
            vision = VisionModule(cfg)
        else:
            raise RuntimeError(f"MTG_VISION must be camera, files or off (got {settings.vision!r})")
        if "MTG_LIGHT_BASE_LEVEL" not in os.environ:   # calibrated value, saved in vision.toml
            settings.light_base_level = cfg.camera.light_level
        try:
            vision.start(open_camera=settings.vision == "camera")
        except DatabaseUnavailable as exc:
            # Not fatal: the API still runs and /run/start answers CARD_DB_MISSING (pre-run block).
            log.error("vision not ready: %s", exc)
    return VisionIdentifier(vision, settings.identify_attempts, settings.light_base_level,
                            settings.light_step), vision


def create_app(settings: Settings | None = None, serial_port: SerialPort | None = None,
               vision=None, server_client: ServerClient | None = None) -> FastAPI:
    """vision: a cardforge.cv.VisionModule (or test double) to use instead of MTG_VISION.
    server_client: the link to the CardForge server to use instead of one made for MTG_SERVER_URL."""
    settings = settings or Settings.from_env()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level.upper(),
                            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sim_world = None
        port = serial_port
        if port is None and settings.simulate:
            sim_world = SimWorld(stack_cards=settings.sim_stack_cards, time_scale=settings.sim_time_scale,
                                 bottom_present=settings.bottom_enabled,
                                 optional_commands=settings.sim_optional_commands)
            port = SimSerialPort(sim_world)
        elif port is None:
            port = open_serial(settings)
        direction = None
        if settings.de_rts and not settings.simulate:
            direction = RtsDirectionPin(port, invert=settings.de_rts_invert)
        elif settings.de_pin is not None and not settings.simulate:
            direction = GpioDirectionPin(settings.de_pin)
        bus = RS485Bus(port, baudrate=settings.baudrate, direction_pin=direction,
                       reply_timeout=settings.reply_timeout, retries=settings.retries,
                       frame_gap=settings.frame_gap_s)
        hub = EventHub()
        hub.bind_loop(asyncio.get_running_loop())
        store = RunStore(settings.run_db)
        identifier, vision_module = await asyncio.to_thread(build_identifier, settings, vision)
        status_led = build_status_led(settings.led, settings.led_pins, settings.simulate)
        server, worker, alerts = server_client, None, None
        if server is None and settings.server_url:
            server = ServerClient(settings.server_url, settings.machine_token, settings.server_timeout_s)
        if server is not None:
            if not settings.machine_token and server_client is None:
                log.warning("MTG_MACHINE_TOKEN is empty: the server will refuse the run uploads")
            worker = SyncWorker(store, server, settings.sync_interval_s, settings.sync_retry_max_s)
            if settings.server_alerts:
                alerts = AlertForwarder(store, server, settings.alert_max_age_s)
                hub.add_listener(alerts.handle)
        monitor = None
        if settings.network_monitor == "on" or (settings.network_monitor == "auto" and not settings.simulate
                                                and NetworkMonitor.available()):
            monitor = NetworkMonitor(status_led)
            monitor.start()
        controller = SorterController(TopModule(bus), BottomModule(bus), identifier, hub, settings, store,
                                      status_led, on_run_finished=worker.notify if worker else None)
        if isinstance(identifier, VisionIdentifier):
            identifier.set_light = controller.set_chamber_light
        app.state.controller = controller
        app.state.events = hub
        app.state.sim_world = sim_world
        app.state.store = store
        app.state.server = server
        app.state.sync_worker = worker
        app.state.alert_forwarder = alerts
        app.state.status_led = status_led
        # The camera calibration page needs a real VisionModule (camera or photo files).
        app.state.vision_module = vision_module if hasattr(vision_module, "calibration_snapshot") else None
        app.state.crop_samples = []        # card positions measured on the calibration page
        if server is None and not settings.app_password:
            log.warning("MTG_APP_PASSWORD is empty: the app can sign in with any username and password")
        await asyncio.to_thread(controller.power_on)
        if worker is not None:
            worker.start()                 # also uploads runs left over from before a reboot
            log.info("runs are uploaded to the CardForge server at %s", server.base_url)
        if alerts is not None:
            alerts.start()
        log.info("sorter API ready (%s%s, vision=%s)", "SIMULATED hardware" if settings.simulate
                 else f"{settings.serial_port} @ {settings.baudrate} baud",
                 "" if settings.bottom_enabled else ", TOP MODULE ONLY",
                 "test double" if vision is not None else settings.vision)
        try:
            yield
        finally:
            await asyncio.to_thread(controller.shutdown)
            if worker is not None:
                await asyncio.to_thread(worker.stop)
            if alerts is not None:
                await asyncio.to_thread(alerts.stop)
            if server is not None and server_client is None:
                server.close()
            if monitor is not None:
                monitor.stop()
            status_led.close()
            bus.close()
            if vision_module is not None and vision is None:
                vision_module.close()
            store.close()

    app = FastAPI(title="MTG Card Sorter API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def controller_of(request: Request) -> SorterController:
        return request.app.state.controller

    @app.post("/run/start")
    def start_run(request: Request, authorization: Optional[str] = Header(None)):
        """Blocks while the base homes (a few seconds), then answers:
        200 run started, 409 conditions not met / already running, 503 module offline, 500 hardware fault.
        With a CardForge server: 401 SIGN_IN_REQUIRED (no or expired app token),
        503 SERVER_UNREACHABLE / 502 SERVER_ERROR (the run's owner could not be checked)."""
        owner = None
        server: ServerClient | None = request.app.state.server
        if server is not None:
            token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
            try:
                owner = server.whoami(token)
            except NotSignedIn as exc:
                return _not_started(401, "SIGN_IN_REQUIRED", str(exc))
            except ServerUnreachable as exc:
                log.warning("start refused: %s", exc)
                return _not_started(503, "SERVER_UNREACHABLE",
                                    "The machine cannot reach the CardForge server, so the run cannot be saved "
                                    "to your account. Check the machine's network and try again.")
            except ServerError as exc:
                return _not_started(502, "SERVER_ERROR", f"The CardForge server answered with an error: {exc}")
        try:
            run = controller_of(request).start(owner=owner)
        except StartRefused as exc:
            return JSONResponse(status_code=exc.http_status, content={
                "started": False,
                "message": exc.message,
                "problems": [p.to_dict() for p in exc.problems],
            })
        return {"started": True, "message": "Run started.", "run": run.to_dict()}

    @app.post("/run/stop")
    def stop_run(request: Request, immediate: bool = False):
        """Normal stop: a card being fed is finished, any card on the platform is dropped,
        the flap is closed and the motors are stopped, then this answers (a few seconds).
        immediate=true: stop the motors right away (a card may stay on the platform)."""
        try:
            message, snapshot = controller_of(request).stop(immediate=immediate)
        except NoActiveRun:
            return JSONResponse(status_code=409, content={"stopped": False, "message": "No run in progress."})
        return {"stopped": True, "message": message, **snapshot}

    @app.post("/run/resume")
    def resume_run(request: Request):
        """Continue a run paused by a fault, after the user fixed it: re-homes the base,
        checks the platform is clear, then sorts on with the same run (same id and counters)."""
        try:
            run = controller_of(request).resume()
        except NotPaused:
            return JSONResponse(status_code=409, content={"resumed": False, "message": "No paused run to resume."})
        except StartRefused as exc:
            return JSONResponse(status_code=exc.http_status, content={
                "resumed": False, "message": exc.message, "problems": [p.to_dict() for p in exc.problems]})
        return {"resumed": True, "message": "Run resumed.", "run": run.to_dict()}

    @app.get("/runs", tags=["results"])
    def list_runs(request: Request, limit: int = 50, offset: int = 0):
        return {"runs": request.app.state.store.list_runs(max(1, min(limit, 500)), max(0, offset))}

    @app.get("/runs/{run_id}", tags=["results"])
    def run_results(run_id: str, request: Request):
        """Per-bin counts and every card of a finished run (RF22).
        409 while the run is running or paused: no card data during a run (RF23)."""
        results = request.app.state.store.run_results(run_id)
        if results is None:
            return JSONResponse(status_code=404, content={"message": "Unknown run."})
        if results["run"]["status"] in ("running", "paused"):
            return JSONResponse(status_code=409, content={
                "message": "The run is still in progress: its cards are shown when it finishes.",
                "run": {k: results["run"][k] for k in ("run_id", "status", "started_at", "cards_processed")}})
        return results

    @app.get("/inventory/export.csv", tags=["results"])
    def export_inventory(request: Request):
        """Every card of every finished run as CSV (RF24, RFC 4180)."""
        return Response(content=request.app.state.store.export_csv(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="cardforge-inventory.csv"'})

    @app.get("/stack")
    def stack(request: Request):
        """Reads the stack IR on the top module (A1)."""
        try:
            return controller_of(request).stack_status()
        except (BusError, FrameError) as exc:
            return JSONResponse(status_code=503, content={
                "has_cards": None,
                "message": "The top module (A1) is not responding. Check its power and the RS-485 cable.",
                "error": str(exc),
            })

    @app.get("/run/status")
    def run_status(request: Request):
        return controller_of(request).snapshot()

    @app.get("/hardware/status")
    def hardware_status(request: Request):
        return controller_of(request).hardware_status()

    # ---------------------------------------------------- manual base control
    def _base_call(fn, message):
        try:
            status = fn()
        except ManualRefused as exc:
            return JSONResponse(status_code=exc.http_status,
                                content={"ok": False, "code": exc.code, "message": exc.message})
        except ModuleNotResponding:
            return JSONResponse(status_code=503, content={
                "ok": False, "code": "MODULE_OFFLINE",
                "message": "The bottom module (A2) is not responding. Check its power and the RS-485 cable."})
        except (HardwareFault, ModuleError, FrameError) as exc:
            code, text = _describe_fault(exc)
            status_code = 409 if code in ("NOT_HOMED", "BUSY") else 500
            if code == "NOT_HOMED":
                text = "The base is not homed yet. Call POST /base/home first."
            return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "message": text})
        return {"ok": True, "message": message(status), "base": status}

    @app.get("/base/status", tags=["base"])
    def base_status(request: Request):
        """Live STATUS of the bottom module (A2): homed, tray, steps, sensors."""
        return _base_call(controller_of(request).base_status, lambda s: "Base status.")

    @app.post("/base/home", tags=["base"])
    def base_home(request: Request):
        """Find the reflective strip (tray 3). Blocks until done (up to ~40 s)."""
        return _base_call(controller_of(request).base_home,
                          lambda s: f"Base homed: at tray {s['position']}.")

    @app.post("/base/goto/{tray}", tags=["base"])
    def base_goto(tray: int, request: Request):
        """Turn the base to tray 1-6 (the base must be homed)."""
        if not 1 <= tray <= 6:
            return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_TRAY",
                                                          "message": "Tray must be 1 to 6."})
        return _base_call(lambda: controller_of(request).base_goto(tray),
                          lambda s: f"Base at tray {s['position']}.")

    @app.post("/base/move", tags=["base"])
    def base_move(body: BaseMove, request: Request):
        """Turn the base to any absolute position: {"steps": 1653} or {"degrees": 46.5}."""
        if (body.steps is None) == (body.degrees is None):
            return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_POSITION",
                                                          "message": 'Give exactly one of "steps" or "degrees".'})
        steps = body.steps if body.steps is not None else round(body.degrees * settings.base_steps_per_turn / 360)
        if not -20000 <= steps <= 20000:
            return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_POSITION",
                                                          "message": "Position must be within -20000..20000 steps."})
        return _base_call(lambda: controller_of(request).base_move(steps),
                          lambda s: f"Base at {s['steps']} steps from home"
                                    + (f" (tray {s['position']})." if s['position'] else "."))

    # ---------------------------------------------------- manual flap control (A1)
    def _flap_call(fn, message):
        try:
            status = fn()
        except ManualRefused as exc:
            return JSONResponse(status_code=exc.http_status,
                                content={"ok": False, "code": exc.code, "message": exc.message})
        except ModuleNotResponding:
            return JSONResponse(status_code=503, content={
                "ok": False, "code": "MODULE_OFFLINE",
                "message": "The top module (A1) is not responding. Check its power and the RS-485 cable."})
        except (HardwareFault, ModuleError, FrameError) as exc:
            code, text = _describe_fault(exc)
            return JSONResponse(status_code=500, content={"ok": False, "code": code, "message": text})
        return {"ok": True, "message": message(status), "flap": status}

    @app.get("/flap", tags=["flap"])
    def flap_status(request: Request):
        """The flap's last commanded angle and the open/closed angles the runs use."""
        return _flap_call(controller_of(request).flap_status,
                          lambda s: f"Flap at {s['angle']} degrees.")

    @app.post("/flap", tags=["flap"])
    def flap_move(body: FlapMove, request: Request):
        """Move the flap for testing: {"angle": 5} (any 0-180) or {"position": "open" | "closed"}.
        A card on the platform falls when the flap opens. The flap stays where it was moved."""
        if (body.angle is None) == (body.position is None):
            return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_ANGLE",
                                                          "message": 'Give exactly one of "angle" or "position".'})
        if body.position is not None:
            if body.position not in ("open", "closed"):
                return JSONResponse(status_code=400, content={
                    "ok": False, "code": "BAD_ANGLE", "message": '"position" must be "open" or "closed".'})
            angle = settings.flap_open_angle if body.position == "open" else settings.flap_closed_angle
        else:
            angle = body.angle
        return _flap_call(lambda: controller_of(request).flap_set(angle),
                          lambda s: f"Flap at {s['angle']} degrees.")

    @app.put("/flap/angles", tags=["flap"])
    def flap_angles(body: FlapAngles, request: Request):
        """Change the open/closed angles the runs use, live. Not saved: set
        MTG_FLAP_OPEN_ANGLE / MTG_FLAP_CLOSED_ANGLE to keep them after a restart."""
        if body.open_angle is None and body.closed_angle is None:
            return JSONResponse(status_code=400, content={
                "ok": False, "code": "BAD_ANGLE", "message": 'Give "open_angle", "closed_angle" or both.'})
        return _flap_call(lambda: controller_of(request).flap_set_angles(body.open_angle, body.closed_angle),
                          lambda s: f"Runs now open the flap to {s['open_angle']} and close it to "
                                    f"{s['closed_angle']} degrees (until the API restarts).")

    @app.get("/health")
    def health(request: Request):
        return {"ok": True, "simulate": settings.simulate, "bottom_enabled": settings.bottom_enabled,
                "server": request.app.state.server is not None}

    @app.get("/sync/status", tags=["results"])
    def sync_status(request: Request):
        """Finished runs: synced (on the server), pending / failed (waiting for an upload),
        local_only (started without a server: they stay on the Pi)."""
        server = request.app.state.server
        return {"enabled": server is not None, "server_url": server.base_url if server else None,
                **request.app.state.store.sync_status()}

    @app.post("/sync/now", tags=["results"])
    def sync_now(request: Request):
        """Upload the finished runs now (also the ones waiting for their next retry)."""
        worker: SyncWorker | None = request.app.state.sync_worker
        if worker is None:
            return JSONResponse(status_code=409, content={"message": "No CardForge server configured (MTG_SERVER_URL)."})
        request.app.state.store.clear_sync_backoff()
        worker.notify()
        return {"message": "Upload started.", **request.app.state.store.sync_status()}

    if not settings.server_url and server_client is None:
        # No CardForge server: the Pi stands in for the app's server routes (login, cards).
        app.include_router(create_app_router(settings))
    app.include_router(create_calibration_router(settings))

    @app.websocket("/ws/events")
    async def events(websocket: WebSocket):
        await websocket.accept()
        hub: EventHub = websocket.app.state.events
        queue = hub.subscribe()
        disconnected = asyncio.create_task(_wait_for_disconnect(websocket))
        try:
            await websocket.send_json({"type": "snapshot", **websocket.app.state.controller.snapshot()})
            while True:
                next_event = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({next_event, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    next_event.cancel()
                    break
                await websocket.send_json(next_event.result())
        except Exception:  # client went away mid-send
            pass
        finally:
            hub.unsubscribe(queue)
            disconnected.cancel()

    if settings.simulate:
        @app.get("/sim/state", tags=["simulator"])
        def sim_state(request: Request):
            """What the simulated machine looks like right now (+ the Pi's status LED)."""
            return {**request.app.state.sim_world.describe(), "status_led": request.app.state.status_led.mode}

        @app.put("/sim/state", tags=["simulator"])
        def set_sim_state(update: SimStateUpdate, request: Request):
            """Load cards, fill/empty trays, jam the feeder, unplug a module... to test the app."""
            world: SimWorld = request.app.state.sim_world
            world.set_state(**update.model_dump(exclude_none=True))
            return world.describe()

    return app


def _not_started(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "started": False, "message": message, "problems": [{"code": code, "message": message}]})


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
