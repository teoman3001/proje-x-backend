"""Clara Kontrol Merkezi — POST /execute."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from clara.client import ClaraClient
from clara.executor import execute
from clara.security import (
    check_rate_limit,
    client_id_for_rate_limit,
    command_key,
    is_command_allowed,
    load_clara_config,
    log_command,
    verify_clara_api_key,
)
from schemas import ExecuteBody

router = APIRouter(tags=["clara"])


@router.post("/execute")
async def clara_execute(
    request: Request,
    body: ExecuteBody,
    _api_key: str = Depends(verify_clara_api_key),
):
    cfg = load_clara_config()
    cid = client_id_for_rate_limit(request, _api_key)
    check_rate_limit(cfg, cid)

    if not is_command_allowed(cfg, body.command, body.action):
        result = {
            "success": False,
            "error": "Komut izin listesinde yok",
            "error_code": "FORBIDDEN_COMMAND",
            "details": command_key(body.command, body.action),
        }
        log_command(cfg, client_id=cid, body=body.model_dump(), result=result)
        return result

    result = await asyncio.to_thread(
        lambda: execute(
            body.command,
            body.action,
            body.params,
            file_root=cfg["file_root"],
        )
    )
    log_command(cfg, client_id=cid, body=body.model_dump(), result=result)

    if body.task_id:
        ClaraClient().send_result(body.task_id, result)

    return result
