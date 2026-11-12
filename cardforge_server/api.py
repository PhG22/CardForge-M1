"""CardForge server API (DB01): accounts, run upload from the sorter, results and collection.

    GET  /health               liveness + database check
    POST /auth/signup          create an account -> access token            (app)
    POST /auth/login           username or email + password -> access token (app)
    POST /auth/google          not configured: 501                          (app)
    GET  /auth/me              who the token belongs to        (app, and the Pi at run start)
    POST /sync/runs            one finished run + its cards     (the Pi's SyncWorker, machine token)
    GET  /runs                 the user's runs, newest first    (app)
    GET  /runs/{id}            per-tray counts + every card of one run (RF22)
    GET  /cards                the user's collection, grouped by name, with trays ("Cards in folders")
    GET  /cards/export         every card the user owns, rows for the app's CSV export (RF24)
    POST /machine/events       an alert of a run: fault / run end (the Pi, machine token)  (RF38)
    GET  /notifications        the user's alerts after an id (of one run: run_id); waits up to
                               `wait` s for a new one
                               (the app's background service: notifications with the app closed)

App routes take `Authorization: Bearer <access token>`; /sync/runs and /machine/events take
`Authorization: Bearer <machine token>`. Errors are {"code": ..., "message": ...}.

Run with:  python -m cardforge_server        (settings from CF_* env variables)
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import repository as repo
from .config import ServerSettings
from .db import migrate, open_pool
from .notifications import NotificationBroker
from .security import (bearer, burn_verify_time, hash_password, issue_token, machine_token_ok, needs_rehash,
                       read_token, verify_password)

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN, PASSWORD_MAX = 8, 128


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# ------------------------------------------------------------------ bodies

class SignUpBody(BaseModel):
    username: str
    email: str
    password: str


class LoginBody(BaseModel):
    identifier: str
    password: str


class SyncRun(BaseModel):
    local_uuid: UUID
    started_by: int
    status: Literal["completed", "failed", "cancelled"]      # only finished runs are uploaded
    started_at: datetime
    stopped_at: datetime
    cards_processed: int = Field(ge=0)
    successful_scans: int = Field(ge=0)
    failed_scans: int = Field(ge=0)
    error_code: Optional[str] = None
    message: Optional[str] = None


class SyncCard(BaseModel):
    local_uuid: UUID
    owner_id: int
    card_number: int = Field(ge=1)
    scan_status: Literal["identified", "unidentified", "face_down"]
    bin: Optional[int] = Field(None, ge=1, le=6)
    card_identifier: Optional[str] = None
    card_name: Optional[str] = None
    set_code: Optional[str] = None
    collector_number: Optional[str] = None
    color_identity: Optional[str] = None
    type_line: Optional[str] = None
    mana_cost: Optional[str] = None
    rarity: Optional[str] = None
    match_method: Optional[str] = None
    confidence: Optional[int] = None
    attempts: Optional[int] = None
    scanned_at: datetime
    error_message: Optional[str] = None


class SyncBody(BaseModel):
    run: SyncRun
    cards: List[SyncCard] = Field(default_factory=list)


class MachineEventBody(BaseModel):
    """RF38: one alert of a run, sent by the Pi when it happens."""
    event_id: UUID                                    # chosen by the Pi: a resend is stored once
    owner_id: int                                     # the user who started the run
    run_id: Optional[UUID] = None                     # local_run.local_uuid
    kind: Literal["fault", "run_ended"]
    code: Optional[str] = Field(None, max_length=64)  # fault code / error code of the end
    status: Optional[Literal["completed", "cancelled", "failed"]] = None
    message: Optional[str] = Field(None, max_length=500)
    cards_processed: Optional[int] = Field(None, ge=0)
    time: datetime


NOTIFICATION_WAIT_MAX_S = 30.0


# ------------------------------------------------------------------ app

def create_app(settings: ServerSettings | None = None) -> FastAPI:
    settings = settings or ServerSettings.from_env()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level.upper(),
                            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    secret = settings.token_secret
    if not secret:
        secret = secrets.token_urlsafe(48)
        log.warning("CF_TOKEN_SECRET is empty: using a random secret, users must sign in again after a restart")
    if not settings.machine_token_list:
        log.warning("CF_MACHINE_TOKENS is empty: no sorter machine can upload runs")
    ttl_s = settings.token_ttl_hours * 3600
    broker = NotificationBroker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_migrate:
            applied = migrate(settings.database_url)
            if applied:
                log.info("database migrated: %s", ", ".join(applied))
        app.state.pool = open_pool(settings.database_url, settings.pool_max_size)
        broker.bind_loop(asyncio.get_running_loop())
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="CardForge server", version="1.0.0", lifespan=lifespan)
    app.state.notifications = broker
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, **exc.extra})

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()]
        return JSONResponse(status_code=422, content={
            "code": "INVALID_REQUEST", "message": "Invalid request: " + ", ".join(f for f in fields if f),
            "errors": [{"field": f, "message": e.get("msg")} for f, e in zip(fields, exc.errors())]})

    def db(request: Request):
        with request.app.state.pool.connection() as conn:     # one transaction per request
            yield conn

    def current_user(authorization: Optional[str] = Header(None), conn=Depends(db)) -> dict:
        """DB05: validate the bearer token, then load the user (it may have been disabled since)."""
        token = bearer(authorization)
        user_id = read_token(token, secret) if token else None
        user = repo.get_user(conn, user_id) if user_id is not None else None
        if user is None or not user["is_active"]:
            raise ApiError(401, "UNAUTHORIZED", "Sign in again.")
        return user

    def machine(authorization: Optional[str] = Header(None)) -> None:
        if not machine_token_ok(bearer(authorization), settings.machine_token_list):
            raise ApiError(401, "BAD_MACHINE_TOKEN", "Unknown machine token.")

    def session(user: dict) -> dict:
        return {"access_token": issue_token(user["id"], user["username"], secret, ttl_s),
                "token_type": "bearer", "expires_in": int(ttl_s), "user": repo.public_user(user)}

    # -------------------------------------------------------------- health

    @app.get("/health")
    def health(conn=Depends(db)):
        conn.execute("SELECT 1")
        return {"ok": True, "database": True}

    # -------------------------------------------------------------- auth (DB03)

    @app.post("/auth/signup", status_code=201)
    def signup(body: SignUpBody, conn=Depends(db)):
        username, email = body.username.strip(), body.email.strip()
        if not USERNAME_RE.match(username):
            raise ApiError(422, "INVALID_USERNAME",
                           "The username must have 3 to 32 letters, digits, '.', '_' or '-'.", field="username")
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise ApiError(422, "INVALID_EMAIL", "Invalid email address.", field="email")
        if not PASSWORD_MIN <= len(body.password) <= PASSWORD_MAX:
            raise ApiError(422, "WEAK_PASSWORD",
                           f"The password must have {PASSWORD_MIN} to {PASSWORD_MAX} characters.", field="password")
        try:
            user = repo.create_user(conn, username, email, hash_password(body.password))
        except repo.UniqueTaken as exc:
            if exc.field == "email":
                raise ApiError(409, "EMAIL_TAKEN", "There is already an account with this email.", field="email")
            raise ApiError(409, "USERNAME_TAKEN", "This username is taken.", field="username")
        repo.record_login(conn, user["id"])
        log.info("new user %s (id %s)", user["username"], user["id"])
        return session(user)

    @app.post("/auth/login")
    def login(body: LoginBody, conn=Depends(db)):
        row = repo.find_login(conn, body.identifier.strip())
        if row is None:
            burn_verify_time(body.password)
            raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
        if not verify_password(row["password_hash"], body.password):
            raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
        if not row["is_active"]:
            raise ApiError(403, "ACCOUNT_DISABLED", "This account is disabled.")
        repo.record_login(conn, row["id"], hash_password(body.password) if needs_rehash(row["password_hash"]) else None)
        return session(row)

    @app.post("/auth/google")
    def google_login():
        raise ApiError(501, "GOOGLE_NOT_CONFIGURED",
                       "Google sign-in is not configured on this server. Sign in with username and password.")

    @app.get("/auth/me")
    def me(user: dict = Depends(current_user)):
        return repo.public_user(user)

    # -------------------------------------------------------------- run upload (DB02 / DB04)

    @app.post("/sync/runs", dependencies=[Depends(machine)])
    def sync_run(body: SyncBody, conn=Depends(db)):
        """One finished run and all its cards, in one transaction. Safe to retry: rows already
        uploaded are matched by local_uuid and never duplicated."""
        run, cards = body.run, body.cards
        _check_run(run, cards)
        if repo.get_user(conn, run.started_by) is None:
            raise ApiError(422, "UNKNOWN_USER", f"User {run.started_by} does not exist on this server.")
        try:
            with conn.transaction():
                saved = repo.upsert_run(conn, run.model_dump())
                if saved["started_by"] != run.started_by:
                    raise ApiError(409, "RUN_OWNER_MISMATCH", "This run was already uploaded for another user.")
                rows = [_card_row(c) for c in cards]
                inserted = repo.insert_cards(conn, saved["id"], rows)
        except psycopg.errors.IntegrityError as exc:
            raise ApiError(422, "INVALID_RUN", f"The run was rejected by the database: {exc.diag.message_primary}")
        log.info("run %s uploaded as #%s: %d card(s), %d new", run.local_uuid, saved["id"], len(cards), inserted)
        return {"run_id": saved["id"], "created": saved["created"], "cards_received": len(cards),
                "cards_inserted": inserted}

    # -------------------------------------------------------------- results (DB05)

    @app.get("/runs")
    def runs(limit: int = 50, offset: int = 0, user: dict = Depends(current_user), conn=Depends(db)):
        items, total = repo.list_runs(conn, user["id"], max(1, min(limit, 200)), max(0, offset))
        return {"runs": items, "total": total}

    @app.get("/runs/{run_id}")
    def run_results(run_id: int, user: dict = Depends(current_user), conn=Depends(db)):
        results = repo.run_results(conn, user["id"], run_id)
        if results is None:
            raise ApiError(404, "RUN_NOT_FOUND", "Run not found.")
        return results

    @app.get("/cards")
    def cards(query: str = "", limit: int = 30, offset: int = 0,
              user: dict = Depends(current_user), conn=Depends(db)):
        items, total = repo.collection(conn, user["id"], query.strip(), max(1, min(limit, 200)), max(0, offset))
        return {"items": items, "total": total}

    @app.get("/cards/export")
    def export(user: dict = Depends(current_user), conn=Depends(db)):
        rows = repo.export_rows(conn, user["id"])
        return {"rows": rows, "total_cards": sum(r["quantity"] for r in rows)}

    # -------------------------------------------------------------- alerts (RF38)

    @app.post("/machine/events", status_code=201, dependencies=[Depends(machine)])
    def machine_event(body: MachineEventBody, request: Request):
        """An alert of a run (a fault that paused it, or its end), for the user who started it."""
        if body.kind == "fault" and not body.code:
            raise ApiError(422, "INVALID_EVENT", "A fault needs its code.")
        if body.kind == "run_ended" and body.status is None:
            raise ApiError(422, "INVALID_EVENT", "The end of a run needs its status.")
        # Own transaction, committed before the waiting apps are woken up.
        with request.app.state.pool.connection() as conn:
            if repo.get_user(conn, body.owner_id) is None:
                raise ApiError(422, "UNKNOWN_USER", f"User {body.owner_id} does not exist on this server.")
            notification_id, created = repo.insert_notification(conn, body.owner_id, body.model_dump())
        if created:
            broker.publish(body.owner_id)
            log.info("alert #%s for user %s: %s %s", notification_id, body.owner_id, body.kind,
                     body.code or body.status)
        return {"id": notification_id, "created": created}

    @app.get("/notifications")
    async def notifications(request: Request, after: Optional[int] = None, wait: float = 0, limit: int = 50,
                            run_id: Optional[UUID] = None, authorization: Optional[str] = Header(None)):
        """The user's alerts with an id above `after` (oldest first) and the id to ask after next.
        run_id: only the alerts of that run (the app's background service watches one run).
        Without `after`: no alerts, only the current last id (where a new watcher starts).
        With `wait` > 0 and nothing new yet, the request waits up to `wait` seconds (max 30)
        for the next alert. No database connection is held while it waits."""
        token = bearer(authorization)
        user_id = read_token(token, secret) if token else None
        if user_id is None:
            raise ApiError(401, "UNAUTHORIZED", "Sign in again.")
        pool = request.app.state.pool
        limit = max(1, min(limit, 200))
        wait = max(0.0, min(wait, NOTIFICATION_WAIT_MAX_S))

        def load():
            with pool.connection() as conn:
                user = repo.get_user(conn, user_id)
                if user is None or not user["is_active"]:
                    return None
                last = repo.last_notification_id(conn, user_id)
                if after is None:
                    return [], last
                items = repo.notifications_after(conn, user_id, max(0, after), limit,
                                                 str(run_id) if run_id else None)
                # An `after` above the last id (database recreated): start again from the last id.
                return items, (items[-1]["id"] if items else min(after, last))

        waiter = broker.subscribe(user_id)
        try:
            result = await run_in_threadpool(load)
            if result is None:
                raise ApiError(401, "UNAUTHORIZED", "Sign in again.")
            items, last = result
            if not items and after is not None and wait > 0 and await broker.wait(waiter, wait):
                result = await run_in_threadpool(load)
                if result is None:
                    raise ApiError(401, "UNAUTHORIZED", "Sign in again.")
                items, last = result
        finally:
            broker.unsubscribe(user_id, waiter)
        return {"notifications": items, "last_id": last}

    return app


def _check_run(run: SyncRun, cards: list[SyncCard]) -> None:
    """RNF20: the counters must match the cards, one card per number, all owned by the run's user."""
    ok = sum(c.scan_status == "identified" for c in cards)
    if (run.cards_processed, run.successful_scans, run.failed_scans) != (len(cards), ok, len(cards) - ok):
        raise ApiError(422, "INCONSISTENT_RUN",
                       f"The run counters ({run.cards_processed} processed, {run.successful_scans} ok, "
                       f"{run.failed_scans} failed) do not match the {len(cards)} card(s) sent.")
    if len({c.card_number for c in cards}) != len(cards):
        raise ApiError(422, "INCONSISTENT_RUN", "Two cards have the same number.")
    if any(c.owner_id != run.started_by for c in cards):
        raise ApiError(422, "INCONSISTENT_RUN", "Every card must belong to the user who started the run.")
    if any(c.scan_status == "identified" and not (c.card_identifier or "").strip() for c in cards):
        raise ApiError(422, "INCONSISTENT_RUN", "An identified card has no card_identifier.")


def _card_row(c: SyncCard) -> dict:
    row = c.model_dump(mode="json")
    identified = c.scan_status == "identified"
    row["scan_status"] = "success" if identified else "failed"
    row["flag"] = None if identified else c.scan_status
    if identified:
        row["error_message"] = None                       # cards_error_only_on_failure
    else:
        row["card_identifier"] = (c.card_identifier or "").strip() or None
    return row
