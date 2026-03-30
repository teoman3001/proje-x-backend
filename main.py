import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

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
from fastapi.staticfiles import StaticFiles

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import Base, SessionLocal, engine, get_db
from models import Chat, Message, User
from schemas import ChatOut, MessageCreate, MessageOut

TEOMAN = "Teoman"
CLARA = "Clara"
ALLOWED_SENDERS = {TEOMAN.lower(): TEOMAN, CLARA.lower(): CLARA}

UPLOAD_DIR = Path.home() / "Projects" / "clara-dev" / "proje-x-files"
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
    base = Path(name).name
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı.")
    return base


def _extension_ok(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_FILE_EXTENSIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
    dest = UPLOAD_DIR / name
    total = 0
    try:
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
    return {"filename": name, "url": f"/files/{name}"}


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


app.mount(
    "/files",
    StaticFiles(directory=str(UPLOAD_DIR)),
    name="files",
)
