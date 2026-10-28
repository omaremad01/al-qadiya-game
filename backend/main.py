import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, llm
from .config import CASE_SECRET, DATA_DIR, GROQ_API_KEY, RATE_CHAT_PER_MIN, RATE_NEW_PER_HOUR

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("qadiya")
if not GROQ_API_KEY:
    log.warning("GROQ_API_KEY مش موجود! اعمله ملف .env (شوف .env.example)")

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="القضية")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# القضية بتتشفّر وتتبعت للمتصفح، فالسيرفر ما بيحتفظش بأي حالة للاعبين
# واللاعب مايقدرش يشوف القاتل. ده بيخلّي اللعبة تشتغل صح حتى لو السيرفر عمل restart.
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(CASE_SECRET.encode()).digest()))


def encode_case(case: dict) -> str:
    return _fernet.encrypt(json.dumps(case, ensure_ascii=False).encode()).decode()


def decode_case(token: str) -> dict:
    try:
        return json.loads(_fernet.decrypt(token.encode()))
    except (InvalidToken, ValueError):
        raise HTTPException(400, "ملف القضية مش صالح (ممكن السيرفر اتغيّر له السر). ابدأ قضية جديدة.")


# ---------------------------------------------------------------- rate limit
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def limit(request: Request, bucket: str, max_n: int, window: int):
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = fwd or (request.client.host if request.client else "?")
    now = time.time()
    with _hits_lock:
        q = _hits[(bucket, ip)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= max_n:
            raise HTTPException(429, "بتبعت بسرعة! استنى شوية وكمّل.")
        q.append(now)


# --------------------------------------------------------------- daily case
_daily_lock = threading.Lock()


def today_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_daily_case() -> dict:
    today = today_str()
    f = DATA_DIR / f"daily_{today}.json"
    with _daily_lock:  # عشان لو 10 لاعبين دخلوا مع بعض يتولّد مرة واحدة بس
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        case = agents.generate_case()
        case["case_id"] = f"daily-{today}"
        f.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        for old in sorted(DATA_DIR.glob("daily_*.json"))[:-5]:
            old.unlink(missing_ok=True)
        return case


# -------------------------------------------------------------------- models
class TokenIn(BaseModel):
    token: str = Field(max_length=80000)


class SearchIn(TokenIn):
    location_id: str = Field(max_length=20)


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=700)


class ChatIn(TokenIn):
    suspect_id: str = Field(max_length=20)
    message: str = Field("", max_length=400)
    history: List[Msg] = Field(default_factory=list, max_length=30)
    evidence_id: Optional[str] = Field(None, max_length=20)
    lab_note: Optional[str] = Field(None, max_length=500)


class AccuseIn(TokenIn):
    suspect_id: str = Field(max_length=20)
    reasoning: str = Field("", max_length=800)
    questions: int = Field(0, ge=0, le=500)


class LabIn(TokenIn):
    clue_id: str = Field(max_length=20)
    test: Literal["fingerprints", "dna", "chemical", "digital", "handwriting", "timeline"]


class HintIn(TokenIn):
    found_ids: List[str] = Field(default_factory=list, max_length=12)
    searched: List[str] = Field(default_factory=list, max_length=8)
    hints_given: int = Field(0, ge=0, le=20)


class DebriefIn(TokenIn):
    suspect_id: str = Field(max_length=20)
    reasoning: str = Field("", max_length=800)
    score: int = Field(0, ge=0, le=100)
    message: str = Field(min_length=1, max_length=400)
    history: List[Msg] = Field(default_factory=list, max_length=30)


@app.exception_handler(llm.LLMError)
async def llm_error_handler(request: Request, exc: llm.LLMError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ----------------------------------------------------------------- endpoints
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "groq_key": bool(GROQ_API_KEY)}


@app.post("/api/case/daily")
def case_daily(request: Request):
    limit(request, "daily", 30, 3600)
    case = get_daily_case()
    return {"token": encode_case(case), "case": agents.public_view(case)}


@app.post("/api/case/new")
def case_new(request: Request):
    limit(request, "new", RATE_NEW_PER_HOUR, 3600)
    case = agents.generate_case()
    case["case_id"] = uuid.uuid4().hex[:10]
    return {"token": encode_case(case), "case": agents.public_view(case)}


@app.post("/api/search")
def search(body: SearchIn, request: Request):
    limit(request, "search", 40, 60)
    case = decode_case(body.token)
    try:
        return {"clues": agents.search_location(case, body.location_id)}
    except KeyError:
        raise HTTPException(404, "المكان ده مش موجود.")


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    limit(request, "chat", RATE_CHAT_PER_MIN, 60)
    if not body.message.strip() and not body.evidence_id:
        raise HTTPException(400, "اكتب سؤال أو اعرض دليل.")
    case = decode_case(body.token)
    try:
        return agents.suspect_reply(
            case, body.suspect_id,
            [m.model_dump() for m in body.history], body.message, body.evidence_id, body.lab_note)
    except KeyError:
        raise HTTPException(404, "المشتبه فيه أو الدليل مش موجود.")


@app.post("/api/accuse")
def accuse(body: AccuseIn, request: Request):
    limit(request, "accuse", 10, 60)
    case = decode_case(body.token)
    try:
        return agents.judge(case, body.suspect_id, body.reasoning, body.questions)
    except KeyError:
        raise HTTPException(404, "المشتبه فيه مش موجود.")


@app.post("/api/debrief")
def debrief(body: DebriefIn, request: Request):
    limit(request, "debrief", RATE_CHAT_PER_MIN, 60)
    case = decode_case(body.token)
    try:
        return agents.debrief(case, body.suspect_id, body.reasoning, body.score,
                              [m.model_dump() for m in body.history], body.message)
    except KeyError:
        raise HTTPException(404, "المشتبه فيه مش موجود.")


@app.post("/api/lab")
def lab(body: LabIn, request: Request):
    limit(request, "lab", 15, 60)
    case = decode_case(body.token)
    try:
        return agents.lab_analyze(case, body.clue_id, body.test)
    except KeyError:
        raise HTTPException(404, "الدليل ده مش موجود.")


@app.post("/api/hint")
def hint(body: HintIn, request: Request):
    limit(request, "hint", 8, 60)
    case = decode_case(body.token)
    return agents.hint(case, body.found_ids, body.searched, body.hints_given)
