"""API key, whitelist, rate limit, komut loglama."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Header, HTTPException, Request, WebSocket

DEFAULT_ALLOWED = (
    "terminal.exec,file.read,file.write,file.list,app.launch,app.kill,"
    "peekaboo.screenshot,peekaboo.click,peekaboo.type,system.info"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_clara_config() -> dict:
    return {
        "api_key": os.environ.get("CLARA_API_KEY", "").strip(),
        "allowed_commands": _parse_allowed(
            os.environ.get("CLARA_ALLOWED_COMMANDS", DEFAULT_ALLOWED)
        ),
        "rate_limit_per_minute": _env_int("CLARA_RATE_LIMIT", 60),
        "log_file": os.environ.get("CLARA_LOG_FILE", "logs/clara_commands.log"),
        "file_root": Path(
            os.environ.get("CLARA_FILE_ROOT", str(Path.home()))
        ).expanduser().resolve(),
        "allow_unconfigured_key": os.environ.get("CLARA_ALLOW_UNCONFIGURED_KEY", "")
        .lower()
        in ("1", "true", "yes"),
    }


def _parse_allowed(raw: str) -> frozenset[str]:
    parts = [p.strip() for p in raw.replace(" ", "").split(",") if p.strip()]
    return frozenset(parts)


def command_key(command: str, action: str) -> str:
    return f"{command.strip().lower()}.{action.strip().lower()}"


def is_command_allowed(cfg: dict, command: str, action: str) -> bool:
    key = command_key(command, action)
    return key in cfg["allowed_commands"]


_rate_buckets: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(cfg: dict, client_id: str) -> None:
    limit = max(1, int(cfg["rate_limit_per_minute"]))
    window = 60.0
    now = time.time()
    bucket = _rate_buckets[client_id]
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "success": False,
                "error": "Çok fazla istek",
                "error_code": "RATE_LIMIT",
                "details": f"Dakikada en fazla {limit} istek.",
            },
        )
    bucket.append(now)


def log_command(cfg: dict, *, client_id: str, body: dict, result: dict) -> None:
    path = Path(cfg["log_file"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\t"
            f"{client_id}\t{body.get('command')}.{body.get('action')}\t"
            f"success={result.get('success')}\n"
        )
        path.open("a", encoding="utf-8").write(line)
    except OSError:
        pass


async def verify_clara_api_key(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    cfg = load_clara_config()
    key = (x_api_key or "").strip()
    expected = cfg["api_key"]

    if not expected:
        if cfg["allow_unconfigured_key"]:
            return "dev"
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": "Sunucu yapılandırması eksik",
                "error_code": "MISSING_API_KEY",
                "details": "CLARA_API_KEY ortam değişkeni ayarlanmalı.",
            },
        )

    if key != expected:
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": "Geçersiz API anahtarı",
                "error_code": "UNAUTHORIZED",
                "details": "X-API-Key başlığı doğru değil.",
            },
        )
    return key


def client_id_for_rate_limit(request: Request, api_key_hash: str) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return f"{ip}:{api_key_hash[:16]}"


def client_id_for_websocket(websocket: WebSocket, token: str) -> str:
    c = websocket.client
    ip = c.host if c else "unknown"
    return f"ws:{ip}:{(token or '')[:16]}"
