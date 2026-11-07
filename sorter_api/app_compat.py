"""The routes the CardForge mobile app already calls (its lib/core/api/api_contract.dart),
served by the Pi so the app works with API_BASE_URL=http://<raspberry-ip>:8000.

    POST /auth/login             {identifier, password} -> {access_token}
    POST /auth/google            not available on the machine (needs the CardForge server)
    GET  /cards                  ?query=&limit=&offset= -> {items: [{id, name, folders}], total}
    GET  /machines/{id}/status   -> {state: idle | running | stopped}
    POST /machines/{id}/start    start a run, or resume a paused one -> {state}
    POST /machines/{id}/stop     normal stop -> {state}

The machine id in the path is ignored: one Pi drives one machine.

App state mapping:
    idle    = no run in progress
    running = starting, sorting or stopping
    stopped = paused by a fault (feed jam, stuck card...): Start/Resume continues the same run

Start and resume home the base, which takes 5-40 s, but the app gives up after 10 s.
These routes wait up to APP_WAIT_S: a refusal (no cards, trays not empty...) comes
back within that time as 409; after it the answer is "running" and the result
shows up in the next status (and on the WebSocket).
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .controller import ControllerState, NoActiveRun, NotPaused, SorterController, StartRefused, utc_now

log = logging.getLogger(__name__)

APP_WAIT_S = 8.0

APP_STATE = {
    ControllerState.IDLE: "idle",
    ControllerState.STARTING: "running",
    ControllerState.RUNNING: "running",
    ControllerState.STOPPING: "running",
    ControllerState.MANUAL: "running",
    ControllerState.PAUSED: "stopped",
}


class LoginBody(BaseModel):
    identifier: str
    password: str


def create_app_router(settings) -> APIRouter:
    router = APIRouter(tags=["app"])

    def store_of(request: Request):
        return request.app.state.store

    def controller_of(request: Request) -> SorterController:
        return request.app.state.controller

    def unauthorized():
        return JSONResponse(status_code=401, content={"message": "Sign in again."})

    def signed_in(request: Request, authorization: str | None) -> bool:
        if not authorization or not authorization.lower().startswith("bearer "):
            return False
        return store_of(request).token_user(authorization[7:].strip()) is not None

    def app_state(controller: SorterController) -> dict:
        return {"state": APP_STATE[controller.state]}

    def run_with_deadline(fn):
        """Run fn in a thread; wait at most APP_WAIT_S. Returns (finished, exception)."""
        box: dict = {}

        def target():
            try:
                fn()
            except Exception as exc:          # handed back to the request
                box["error"] = exc

        t = threading.Thread(target=target, name="app-command", daemon=True)
        t.start()
        t.join(APP_WAIT_S)
        return not t.is_alive(), box.get("error")

    @router.post("/auth/login")
    def login(body: LoginBody, request: Request):
        ok_user = body.identifier.strip().lower() == settings.app_username.lower()
        if settings.app_password and not (ok_user and body.password == settings.app_password):
            return JSONResponse(status_code=401, content={"message": "Invalid username or password."})
        return {"access_token": store_of(request).issue_token(body.identifier.strip(), utc_now())}

    @router.post("/auth/google")
    def google_login():
        return JSONResponse(status_code=501, content={
            "message": "Google sign-in needs the CardForge server. Sign in with username and password."})

    @router.get("/cards")
    def cards(request: Request, query: str = "", limit: int = 30, offset: int = 0,
              authorization: str | None = Header(default=None)):
        if not signed_in(request, authorization):
            return unauthorized()
        items, total = store_of(request).inventory(query.strip(), max(1, min(limit, 200)), max(0, offset))
        return {"items": items, "total": total}

    @router.get("/machines/{machine_id}/status")
    def machine_status(machine_id: str, request: Request, authorization: str | None = Header(default=None)):
        if not signed_in(request, authorization):
            return unauthorized()
        return app_state(controller_of(request))

    @router.post("/machines/{machine_id}/start")
    def machine_start(machine_id: str, request: Request, authorization: str | None = Header(default=None)):
        if not signed_in(request, authorization):
            return unauthorized()
        controller = controller_of(request)
        resume = controller.state is ControllerState.PAUSED
        finished, error = run_with_deadline(controller.resume if resume else controller.start)
        if isinstance(error, StartRefused):
            return JSONResponse(status_code=409, content={
                **app_state(controller), "message": error.message,
                "problems": [p.to_dict() for p in error.problems]})
        if isinstance(error, NotPaused):
            return app_state(controller)
        if error is not None:
            log.error("start from the app failed: %s", error)
            return JSONResponse(status_code=500, content={**app_state(controller), "message": str(error)})
        return app_state(controller)

    @router.post("/machines/{machine_id}/stop")
    def machine_stop(machine_id: str, request: Request, authorization: str | None = Header(default=None)):
        if not signed_in(request, authorization):
            return unauthorized()
        controller = controller_of(request)
        finished, error = run_with_deadline(controller.stop)
        if error is not None and not isinstance(error, NoActiveRun):
            log.error("stop from the app failed: %s", error)
            return JSONResponse(status_code=500, content={**app_state(controller), "message": str(error)})
        return app_state(controller)

    return router
