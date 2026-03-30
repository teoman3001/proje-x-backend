from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    messages: Mapped[list["Message"]] = relationship(back_populates="sender")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user1_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user2_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    messages: Mapped[list["Message"]] = relationship(back_populates="chat")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship(back_populates="messages")
