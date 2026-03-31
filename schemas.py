from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    sender: str = Field(..., description="Teoman veya Clara")
    content: str = Field(..., min_length=1)


class MessageOut(BaseModel):
    id: int
    chat_id: int
    sender_id: int
    sender_name: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatOut(BaseModel):
    id: int
    user1_id: int
    user2_id: int
    user1_name: str
    user2_name: str


class ExecuteBody(BaseModel):
    command: str = Field(..., description="Örn: terminal, file, peekaboo")
    action: str = Field(..., description="Örn: exec, read, screenshot")
    params: dict[str, Any] | None = None
    task_id: str | None = Field(None, description="Opsiyonel görev kimliği (callback)")
