import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import Base, SessionLocal, engine, get_db
from models import Chat, Message, User
from schemas import ChatOut, MessageCreate, MessageOut
from storage import (
    ensure_local_upload_dir,
    guess_content_type,
    resolve_upload_dir,
    sanitize_filename,
    r2_get_object_stream,
    r2_settings,
    r2_upload_file_from_path,
)

TEOMAN = "Teoman"
CLARA = "Clara"
ALLOWED_SENDERS = {TEOMAN.lower(): TEOMAN, CLARA.lower(): CLARA}

UPLOAD_DIR = resolve_upload_dir()
R2_CFG = r2_settings()
USE_R2 = R2_CFG is not None
if not USE_R2:
    ensure_local_upload_dir(UPLOAD_DIR)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_FILE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".pdf", ".doc", ".docx", ".txt"}
)
CHUNK_SIZE = 1024 * 1024


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, text: str) -> None:
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


async def broadcast_message_out(msg: MessageOut) -> None:
    await manager.broadcast(json.dumps(msg.model_dump(mode="json")))


def _safe_filename(name: str | None) -> str:
    if not name:
        raise HTTPException(status_code=400, detail="Dosya adı gerekli.")
    base = sanitize_filename(name)
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı.")
    return base


def _extension_ok(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_FILE_EXTENSIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not USE_R2:
        ensure_local_upload_dir(UPLOAD_DIR)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield


def seed_if_empty(db: Session) -> None:
    teoman = db.execute(select(User).where(User.name == TEOMAN)).scalar_one_or_none()
    clara = db.execute(select(User).where(User.name == CLARA)).scalar_one_or_none()
    if not teoman:
        teoman = User(name=TEOMAN)
        db.add(teoman)
        db.flush()
    if not clara:
        clara = User(name=CLARA)
        db.add(clara)
        db.flush()
    chat = db.execute(select(Chat).limit(1)).scalar_one_or_none()
    if not chat:
        db.add(Chat(user1_id=teoman.id, user2_id=clara.id))
    db.commit()


app = FastAPI(title="Mesajlaşma API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    name = _safe_filename(file.filename)
    if not _extension_ok(name):
        raise HTTPException(
            status_code=400,
            detail="İzin verilmeyen dosya türü. İzinli: jpg, png, gif, mp4, pdf, doc, docx, txt",
        )
    content_type = guess_content_type(name)
    prefix = datetime.utcnow().strftime("%Y/%m/%d")
    key = f"{prefix}/{name}"

    if USE_R2 and R2_CFG is not None:
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="px_", suffix=Path(name).suffix)
            total = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="Dosya çok büyük (en fazla 50MB).",
                        )
                    out.write(chunk)
            r2_upload_file_from_path(R2_CFG, tmp_path, key, content_type)
        except HTTPException:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        pub = R2_CFG.get("public_base", "")
        url = f"{pub}/{key}" if pub else f"/files/{key}"
        return {"filename": name, "path": key, "url": url, "storage": "r2"}

    dest = UPLOAD_DIR / prefix / name
    total = 0
    try:
        ensure_local_upload_dir(dest.parent)
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Dosya çok büyük (en fazla 50MB).",
                    )
                out.write(chunk)
    except HTTPException:
        if dest.exists():
            dest.unlink()
        raise
    return {"filename": name, "path": key, "url": f"/files/{key}", "storage": "local"}


@app.post("/message", response_model=MessageOut)
def send_message(
    body: MessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    key = body.sender.strip().lower()
    if key not in ALLOWED_SENDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Gönderen sadece '{TEOMAN}' veya '{CLARA}' olabilir.",
        )
    sender_name = ALLOWED_SENDERS[key]
    user = db.execute(select(User).where(User.name == sender_name)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="Kullanıcı bulunamadı.")
    chat = db.execute(select(Chat).limit(1)).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=500, detail="Sohbet bulunamadı.")
    msg = Message(
        chat_id=chat.id,
        sender_id=user.id,
        content=body.content.strip(),
        created_at=datetime.utcnow(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    out = MessageOut(
        id=msg.id,
        chat_id=msg.chat_id,
        sender_id=msg.sender_id,
        sender_name=sender_name,
        content=msg.content,
        created_at=msg.created_at,
    )
    background_tasks.add_task(broadcast_message_out, out)
    return out


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


@app.get("/messages", response_model=list[MessageOut])
def list_messages(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Message)
        .options(joinedload(Message.sender))
        .order_by(Message.created_at.asc())
    ).scalars().unique().all()
    return [
        MessageOut(
            id=m.id,
            chat_id=m.chat_id,
            sender_id=m.sender_id,
            sender_name=m.sender.name,
            content=m.content,
            created_at=m.created_at,
        )
        for m in rows
    ]


@app.get("/chats", response_model=list[ChatOut])
def list_chats(db: Session = Depends(get_db)):
    chats = db.execute(select(Chat)).scalars().all()
    if not chats:
        return []
    user_ids = set()
    for c in chats:
        user_ids.add(c.user1_id)
        user_ids.add(c.user2_id)
    users = {
        u.id: u.name
        for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
    }
    return [
        ChatOut(
            id=c.id,
            user1_id=c.user1_id,
            user2_id=c.user2_id,
            user1_name=users[c.user1_id],
            user2_name=users[c.user2_id],
        )
        for c in chats
    ]


if USE_R2 and R2_CFG is not None:

    @app.get("/files/{file_path:path}")
    def download_from_r2(file_path: str):
        rel = Path(file_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise HTTPException(status_code=404, detail="Dosya bulunamadı.")
        safe_name = rel.name
        if not safe_name or not _extension_ok(safe_name):
            raise HTTPException(status_code=404, detail="Dosya bulunamadı.")
        key = "/".join(rel.parts)
        try:
            body, ct = r2_get_object_stream(R2_CFG, key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise HTTPException(status_code=404, detail="Dosya bulunamadı.") from e
            raise HTTPException(status_code=500, detail="Depolama hatası.") from e

        def iter_body():
            try:
                while True:
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                body.close()

        media = ct or guess_content_type(safe_name) or "application/octet-stream"
        return StreamingResponse(iter_body(), media_type=media)

else:
    app.mount(
        "/files",
        StaticFiles(directory=str(UPLOAD_DIR)),
        name="files",
    )
