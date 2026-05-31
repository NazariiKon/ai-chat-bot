from datetime import datetime
from typing import List, Optional
from sqlalchemy import BigInteger, ForeignKey, String, DateTime, Text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64))
    persona: Mapped[Optional[str]] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[List["Message"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"User(id={self.tg_id}, username={self.username})"

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"))
    role: Mapped[str] = mapped_column(String(20))  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"Message(chat_id={self.chat_id}, role={self.role})"

class ChatSettings(Base):
    __tablename__ = "chat_settings"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_nickname: Mapped[Optional[str]] = mapped_column(String(64))
    bot_persona: Mapped[Optional[str]] = mapped_column(Text)
    spontaneous_response_chance: Mapped[float] = mapped_column(Float, default=0.20)
    spontaneous_response_chance: Mapped[float] = mapped_column(Float, default=0.20)
