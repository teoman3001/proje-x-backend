"""Clara için iki yönlü WebSocket: execute mesajları ve sonuç yayını."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from clara.client import ClaraClient
from clara.executor import execute

from clara.security import (
    check_rate_limit,
    client_id_for_websocket,
    command_key,
    is_command_allowed,
    load_clara_config,
    log_command,
)

logger = logging.getLogger("clara.ws")

_clara_ws_connections: list[WebSocket] = []


async def broadcast_clara_event(payload: dict[str, Any]) -> None:
    """Tüm Clara WebSocket istemcilerine JSON gönder."""
    text = json.dumps(payload, default=str)
    stale: list[WebSocket] = []
    for ws in _clara_ws_connections:
        try:
            await ws.send_text(text)
        except Exception:
            stale.append(ws)
    for ws in stale:
        if ws in _clara_ws_connections:
            _clara_ws_connections.remove(ws)


def register_clara_websocket(app) -> None:
    @app.websocket("/ws/clara")
    async def clara_websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        cfg = load_clara_config()
        token = websocket.query_params.get("token") or websocket.query_params.get(
            "api_key", ""
        )
        expected = cfg["api_key"]
        if expected and token != expected:
            await websocket.close(code=4401)
            return
        if not expected and not cfg["allow_unconfigured_key"]:
            await websocket.close(code=4503)
            return

        _clara_ws_connections.append(websocket)
        client = ClaraClient()
        try:
            await websocket.send_text(
                json.dumps({"type": "connected", "message": "Clara WebSocket hazır"})
            )
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "success": False,
                                "error": "Geçersiz JSON",
                                "error_code": "INVALID_JSON",
                            }
                        )
                    )
                    continue

                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                if msg.get("type") != "execute":
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "success": False,
                                "error": "Bilinmeyen mesaj tipi",
                                "error_code": "UNKNOWN_TYPE",
                            }
                        )
                    )
                    continue

                body = msg.get("payload") or msg
                command = body.get("command", "")
                action = body.get("action", "")
                params = body.get("params")

                cid = client_id_for_websocket(websocket, token or "")
                try:
                    check_rate_limit(cfg, cid)
                except HTTPException as e:
                    detail = e.detail
                    if isinstance(detail, dict):
                        await websocket.send_text(
                            json.dumps({"type": "execute_result", **detail})
                        )
                    else:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "execute_result",
                                    "success": False,
                                    "error": str(detail),
                                }
                            )
                        )
                    continue

                if not is_command_allowed(cfg, command, action):
                    result = {
                        "success": False,
                        "error": "Komut izin listesinde yok",
                        "error_code": "FORBIDDEN_COMMAND",
                        "details": command_key(command, action),
                    }
                else:
                    result = execute(
                        command,
                        action,
                        params if isinstance(params, dict) else None,
                        file_root=cfg["file_root"],
                    )

                log_command(
                    cfg,
                    client_id=cid,
                    body={"command": command, "action": action, "params": params},
                    result=result,
                )
                task_id = msg.get("task_id")
                if task_id:
                    client.send_result(str(task_id), result)

                await websocket.send_text(
                    json.dumps({"type": "execute_result", **result}, default=str)
                )
                await broadcast_clara_event(
                    {"type": "clara_event", "command": f"{command}.{action}", "result": result}
                )

        except WebSocketDisconnect:
            pass
        finally:
            if websocket in _clara_ws_connections:
                _clara_ws_connections.remove(websocket)
