from datetime import datetime

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
