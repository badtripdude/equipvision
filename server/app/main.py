import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

import io

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import base64

from . import auth, db, documents, identify, inspection, photos, report, rules, training
from .config import CLIENT_DIR, ROOT_DIR, SERVER_DIR
from .inspection import SIGNS, FrameTracker
from .telemetry import Replayer, find_episodes
from .vision import Detector, decode_jpeg, load_detector

MAX_FRAME_BYTES = 2_000_000

replayer: Replayer
detector: Detector
reader = identify.FrameReader()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global replayer, detector
    db.init_db()
    replayer = Replayer()
    detector = load_detector()
    yield


app = FastAPI(title="EquipVision", lifespan=lifespan)


# ---------- sign-in ----------

OPEN_PATHS = {"/api/login", "/api/logout", "/api/me"}


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Every /api route needs a signed-in user; the static client itself is public."""
    path = request.url.path
    if path.startswith("/api/") and path not in OPEN_PATHS:
        user = await asyncio.to_thread(db.session_user, request.cookies.get(auth.COOKIE))
        if user is None:
            return JSONResponse({"detail": "Нужно войти"}, status_code=401)
        request.state.user = user
    return await call_next(request)


def need(request: Request, permission: str) -> dict:
    user = request.state.user
    if not auth.can(user, permission):
        raise HTTPException(403, f"Недостаточно прав: роль «{user['role_title']}»")
    return user


class LoginIn(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=200)


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    result = db.login(body.username.strip(), body.password)
    if result is None:
        raise HTTPException(401, "Неверный логин или пароль")
    token, user = result
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return {**user, "can": {name: auth.can(user, name) for name in auth.PERMISSIONS}}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    db.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = db.session_user(request.cookies.get(auth.COOKIE))
    if user is None:
        raise HTTPException(401, "Нужно войти")
    return {**user, "can": {name: auth.can(user, name) for name in auth.PERMISSIONS}}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=100)
    role: Literal["engineer", "master", "admin"]
    password: str = Field(min_length=8, max_length=200)


@app.get("/api/users")
def users(request: Request):
    need(request, "manage_users")
    return db.list_users()


@app.post("/api/users", status_code=201)
def users_add(body: UserIn, request: Request):
    need(request, "manage_users")
    try:
        return db.add_user(body.username, body.name.strip(), body.role, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------- equipment card ----------

def component_resource(component: dict, equipment_hours: float) -> dict:
    """Hour-based resource of one component.

    Three things stay separate on purpose: the norm, the calculated remainder and the
    observed condition (the latter comes from inspections, not from this function).
    """
    used = max(0.0, equipment_hours - component["hours_at_install"])
    norm = component["norm_hours"]
    if norm is None:
        return {"used_hours": used, "remaining_hours": None, "status": "unknown",
                "note": "Норматив по наработке не задан: оценка только по состоянию"}
    remaining = norm - used
    if remaining <= 0:
        status = "alarm"
    elif remaining <= 0.1 * norm:
        status = "warn"
    else:
        status = "ok"
    return {"used_hours": used, "remaining_hours": remaining, "status": status, "note": None}


def load_card(equipment_id: str) -> dict:
    card = db.get_equipment(equipment_id)
    if card is None:
        raise HTTPException(404, "Оборудование не найдено")
    # Motor-hours accumulate from telemetry only while the unit runs; without telemetry they stay
    # at the last known value and the card says so.
    card["has_telemetry"] = bool(card["has_telemetry"])
    card["hours"] = card["hours_base"] + (replayer.running_hours() if card["has_telemetry"] else 0)
    card["specs"] = json.loads(card["specs"] or "{}")
    card["maintenance_marks"] = json.loads(card["maintenance_marks"] or "[]")
    for component in card["components"]:
        component["resource"] = component_resource(component, card["hours"])
    card["resource"] = None
    if card["resource_hours"]:
        unit = component_resource({"hours_at_install": 0, "norm_hours": card["resource_hours"]}, card["hours"])
        card["resource"] = {"norm": card["resource_hours"], "used": unit["used_hours"],
                            "remaining": unit["remaining_hours"], "status": unit["status"],
                            "source": card["resource_source"]}
    return card


def diagnose(card: dict) -> dict:
    session = inspection.session(card["id"])
    snapshot = replayer.snapshot()
    ctx = rules.Context(
        values={tag: item["value"] for tag, item in snapshot["values"].items()} if card["has_telemetry"] else {},
        maintenance_marks=tuple(card["maintenance_marks"]),
        unit_resource=card["resource"],
        hours=card["hours"],
        components={c["code"]: c for c in card["components"]},
        events=card["events"],
        findings=session.findings,
        inspected=session.inspected(),
    )
    result = rules.evaluate(ctx)
    result["ts"] = snapshot["ts"]
    result["findings"] = [
        {"component": f.component, "component_name": ctx.component_name(f.component), "sign": f.sign,
         "sign_title": SIGNS[f.sign], "score": f.score, "source": f.source, "ts": f.ts, "note": f.note,
         "photo": f.photo}
        for f in session.findings.values()
    ]
    return result


@app.get("/api/equipment")
def equipment_list():
    return db.list_equipment()


@app.get("/api/equipment/{equipment_id}")
def equipment_card(equipment_id: str):
    return load_card(equipment_id)


class EventIn(BaseModel):
    kind: Literal["inspection", "defect", "maintenance", "replacement"]
    component_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)


@app.post("/api/equipment/{equipment_id}/events", status_code=201)
def equipment_add_event(equipment_id: str, event: EventIn, request: Request):
    # Maintenance and replacement reset resource counters, so they need a higher role.
    user = need(request, "register_work" if event.kind in ("maintenance", "replacement") else "inspect")
    card = load_card(equipment_id)
    try:
        return db.add_event(equipment_id, event.component_id, event.kind, event.note, user["name"], card["hours"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------- identification ----------

@app.post("/api/identify")
async def identify_frame(request: Request):
    """One JPEG frame in the body -> which equipment the camera is looking at.

    QR first (cheap and unambiguous), then the serial number read from the nameplate.
    """
    data = await request.body()
    if not data or len(data) > 4 * MAX_FRAME_BYTES:
        raise HTTPException(400, "Нужен один кадр JPEG")
    started = time.perf_counter()
    try:
        image = await asyncio.to_thread(decode_jpeg, data)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось прочитать кадр: {exc}") from exc
    units = db.list_equipment()

    payload = await asyncio.to_thread(reader.qr, image)
    match = identify.match_qr(payload, units) if payload else None
    by, lines, candidates = ("qr" if match else None), [], []
    if match is None:
        lines = await asyncio.to_thread(reader.text, image)
        match, candidates = identify.match_serial(lines, units)
        by = "serial" if match else None
    return {"match": match, "by": by, "candidates": candidates, "qr": payload, "texts": lines[:8],
            "ms": round((time.perf_counter() - started) * 1000)}


@app.get("/api/equipment/{equipment_id}/qr.png")
def equipment_qr(equipment_id: str):
    if db.get_equipment(equipment_id) is None:
        raise HTTPException(404, "Оборудование не найдено")
    buffer = io.BytesIO()
    identify.make_qr(identify.QR_PREFIX + equipment_id).save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png")


# ---------- diagnosis and the current inspection ----------

@app.get("/api/signs")
def signs():
    return SIGNS


@app.get("/api/equipment/{equipment_id}/diagnosis")
def equipment_diagnosis(equipment_id: str):
    return diagnose(load_card(equipment_id))


class FindingIn(BaseModel):
    component: str
    sign: str
    note: str | None = Field(default=None, max_length=500)
    # The current camera frame as base64 JPEG. Taken by the app from the live video at the
    # moment of recording; there is no way to pick a file.
    frame: str | None = Field(default=None, max_length=4_000_000)


@app.post("/api/equipment/{equipment_id}/findings", status_code=201)
def add_finding(equipment_id: str, finding: FindingIn, request: Request):
    """The engineer records a visual sign by hand."""
    need(request, "inspect")
    card = load_card(equipment_id)
    if finding.component not in {c["code"] for c in card["components"]}:
        raise HTTPException(400, "Нет такого узла у этого оборудования")
    if finding.sign not in SIGNS:
        raise HTTPException(400, "Неизвестный признак")
    photo = None
    if finding.frame:
        try:
            photo = photos.save(decode_jpeg(base64.b64decode(finding.frame, validate=True)))
        except Exception as exc:
            raise HTTPException(400, f"Не удалось сохранить кадр: {exc}") from exc
    old = inspection.session(equipment_id).findings.get((finding.component, finding.sign))
    if old:
        photos.delete(old.photo)
    inspection.add_manual(equipment_id, finding.component, finding.sign, replayer.snapshot()["ts"], finding.note, photo)
    return diagnose(card)


@app.post("/api/equipment/{equipment_id}/inspection/reset")
def inspection_reset(equipment_id: str, request: Request):
    need(request, "inspect")
    card = load_card(equipment_id)
    for f in inspection.session(equipment_id).findings.values():
        photos.delete(f.photo)                    # photos of findings that were never saved
    inspection.reset(equipment_id)
    return diagnose(card)


@app.post("/api/equipment/{equipment_id}/inspection/complete")
def inspection_complete(equipment_id: str, request: Request):
    """Save the walk-around to the equipment history and start a clean inspection."""
    author = need(request, "inspect")["name"]
    card = load_card(equipment_id)
    result = diagnose(card)
    ids = {c["code"]: c["id"] for c in card["components"]}
    names = {c["code"]: c["name"] for c in card["components"]}
    session = inspection.session(equipment_id)

    for f in session.findings.values():
        how = "отмечено инженером" if f.source == "manual" else f"камера, уверенность {round((f.score or 0) * 100)}%"
        note = f"{SIGNS[f.sign].capitalize()} ({how})" + (f". {f.note}" if f.note else "")
        db.add_event(equipment_id, ids[f.component], "defect", note, author, card["hours"], f.sign, f.photo)

    inspected = sorted(names[code] for code in session.inspected())
    lines = [f"Осмотрены узлы: {', '.join(inspected) if inspected else 'нет'}.",
             f"Итог: {result['status_text'].lower()}."]
    lines += [f"{d['title']}: {d['recommendation']}" for d in result["diagnoses"]]
    db.add_event(equipment_id, None, "inspection", " ".join(lines), author, card["hours"])

    inspection.reset(equipment_id)
    on_route = db.round_mark(equipment_id, "inspected")
    return {"saved_findings": len(session.findings), "inspected": inspected, "on_route": on_route,
            "next": db.round_state()["active"]["next"] if on_route else None}


@app.get("/api/photos/{name}")
def photo(name: str):
    target = photos.path(name)
    if target is None:
        raise HTTPException(404, "Нет такого фото")
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------- statistics ----------

def _stats(equipment_id: str | None = None) -> dict:
    data = db.stats(equipment_id)
    data["by_sign"] = [{"sign": sign, "title": SIGNS.get(sign, "без указания признака"), "count": n}
                       for sign, n in data["by_sign"].items()]
    return data


@app.get("/api/stats")
def stats_all():
    return _stats()


@app.get("/api/equipment/{equipment_id}/stats")
def stats_one(equipment_id: str):
    load_card(equipment_id)
    return _stats(equipment_id)


# ---------- walk-around route ----------

@app.get("/api/round")
def round_get():
    return db.round_state()


@app.post("/api/round/start")
def round_start(request: Request):
    return db.round_start(need(request, "inspect")["name"])


class SkipIn(BaseModel):
    equipment_id: str
    reason: str = Field(min_length=3, max_length=300)      # a skipped unit must be explained


@app.post("/api/round/skip")
def round_skip(body: SkipIn, request: Request):
    need(request, "inspect")
    if not db.round_mark(body.equipment_id, "skipped", body.reason.strip()):
        raise HTTPException(404, "Обход не начат или агрегата нет в маршруте")
    return db.round_state()


@app.post("/api/round/finish")
def round_finish(request: Request):
    need(request, "inspect")
    return db.round_finish()


# ---------- trainee mode ----------

@app.get("/api/equipment/{equipment_id}/training")
def training_steps(equipment_id: str):
    return training.steps_for(load_card(equipment_id))


class TrainingIn(BaseModel):
    answers: dict[str, list[str]]


@app.post("/api/equipment/{equipment_id}/training")
def training_submit(equipment_id: str, body: TrainingIn, request: Request):
    user = need(request, "inspect")
    result = training.score(load_card(equipment_id), inspection.session(equipment_id), body.answers)
    db.add_training(equipment_id, user, result)
    return result


@app.get("/api/training")
def training_results(request: Request):
    """Mentors (master, admin) see everyone's results, a trainee sees their own."""
    user = request.state.user
    return db.list_training(None if auth.can(user, "register_work") else user["username"])


# ---------- passports and documentation ----------

@app.get("/api/equipment/{equipment_id}/documents")
def equipment_documents(equipment_id: str):
    return documents.list_for(load_card(equipment_id))


@app.get("/api/equipment/{equipment_id}/documents/{index}/pages/{page}")
def equipment_document_page(equipment_id: str, index: int, page: int, rotate: int = 0):
    card = load_card(equipment_id)
    try:
        data = documents.render_page(card, index, page, rotate)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# ---------- reports ----------

def _report(equipment_id: str, request: Request, kind: str) -> Response:
    card = load_card(equipment_id)
    diagnosis = diagnose(card)
    shots = [(f"Текущий осмотр · {f['component_name']} · {f['sign_title']}", photos.path(f["photo"]))
             for f in diagnosis["findings"]]
    shots += [(f"{e['created_at'][:10]} · {e['component_name'] or 'агрегат'} · {e['note'] or ''}", photos.path(e["photo"]))
              for e in card["events"] if e["kind"] == "defect"]
    rows = report.build_rows(card, diagnosis, request.state.user, _stats(equipment_id),
                             [(caption, path) for caption, path in shots if path][:8])
    try:
        data = report.to_xlsx(rows) if kind == "xlsx" else report.to_pdf(rows)
    except report.NoFont as exc:
        raise HTTPException(503, str(exc)) from exc
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if kind == "xlsx"
             else "application/pdf")
    name = f"report_{card['id']}_{time.strftime('%Y%m%d_%H%M')}.{kind}"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/equipment/{equipment_id}/report.xlsx")
def report_xlsx(equipment_id: str, request: Request):
    return _report(equipment_id, request, "xlsx")


@app.get("/api/equipment/{equipment_id}/report.pdf")
def report_pdf(equipment_id: str, request: Request):
    return _report(equipment_id, request, "pdf")


# ---------- telemetry ----------

@app.get("/api/telemetry/current")
def telemetry_current():
    return replayer.snapshot()


@app.get("/api/telemetry/history")
def telemetry_history(count: int = 48):
    return replayer.history(max(1, min(count, 500)))


@app.get("/api/telemetry/episodes")
def telemetry_episodes():
    return find_episodes(replayer.rows)


class ReplayControl(BaseModel):
    index: int | None = None
    speed: float | None = Field(default=None, ge=0, le=50)
    paused: bool | None = None


@app.post("/api/telemetry/control")
def telemetry_control(control: ReplayControl):
    if control.index is not None:
        replayer.seek(control.index)
    if control.speed is not None:
        replayer.set_speed(control.speed)
    if control.paused is not None:
        replayer.set_paused(control.paused)
    return replayer.snapshot()


# ---------- camera frames ----------

@app.websocket("/ws/vision")
async def vision_socket(ws: WebSocket):
    """Binary message: one JPEG, answered with JSON before the client sends the next one.

    Text message {"type": "focus", "equipment_id": ..., "component": ...}: which component
    the engineer is looking at. Signs confirmed over several frames are stored as findings
    of that component; without a focus frames are analysed but nothing is stored.
    """
    await ws.accept()
    user = await asyncio.to_thread(db.session_user, ws.cookies.get(auth.COOKIE))
    if user is None:
        await ws.close(code=4401, reason="login required")
        return
    may_record = auth.can(user, "inspect")
    seq = 0
    equipment_id: str | None = None
    focus: str | None = None
    tracker = FrameTracker()
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return

        if message.get("text") is not None:
            try:
                payload = json.loads(message["text"])
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "focus":
                card = db.get_equipment(str(payload.get("equipment_id")))
                codes = {c["code"] for c in card["components"]} if card else set()
                component = payload.get("component")
                equipment_id = card["id"] if card else None
                focus = component if component in codes else None
                tracker.reset()
            continue

        data = message.get("bytes") or b""
        seq += 1
        if len(data) > MAX_FRAME_BYTES:
            await ws.send_json({"seq": seq, "error": "Кадр слишком большой"})
            continue
        started = time.perf_counter()
        try:
            image = await asyncio.to_thread(decode_jpeg, data)
            detections = await asyncio.to_thread(detector.detect, image)
        except Exception as exc:  # a broken frame must not drop the connection
            await ws.send_json({"seq": seq, "error": f"Не удалось обработать кадр: {exc}"})
            continue

        recorded: list[str] = []
        expected = inspection.expected_signs(focus) if focus else set(inspection.CAMERA_SIGNS)
        if equipment_id and focus and may_record:
            usual = [d for d in detections if d.label in expected]
            recorded = inspection.observe(equipment_id, focus, tracker.update(usual), replayer.snapshot()["ts"])
            for sign in recorded:                 # keep the frame on which the sign was recorded
                name = await asyncio.to_thread(photos.save, image, [d for d in usual if d.label == sign])
                inspection.session(equipment_id).findings[(focus, sign)].photo = name
        await ws.send_json({
            "seq": seq,
            "width": image.width,
            "height": image.height,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "stub": detector.is_stub,
            "focus": focus,
            "recorded": recorded,
            "detections": [{**d.to_dict(), "unusual": d.label not in expected} for d in detections],
        })


# ---------- development helper ----------

@app.get("/api/dev/photo/{index}")
def dev_photo(index: int):
    """One of the annotated example photos, for checking the pipeline on a machine without a camera.

    Only in development mode (run.py --no-tls). This is a test source for developers,
    not a way to analyse an uploaded photo: the production path is the live camera.
    """
    if os.environ.get("EQUIPVISION_DEV") != "1":
        raise HTTPException(404)
    doc = json.loads((SERVER_DIR / "training" / "annotations.json").read_text(encoding="utf-8"))
    entry = doc["images"][index % len(doc["images"])]
    return FileResponse(ROOT_DIR / doc["root"] / entry["file"], headers={"X-Photo-Name": str(index % len(doc["images"]))})


# Static client last, so it does not shadow the API routes.
app.mount("/", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
