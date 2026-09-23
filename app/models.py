from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        Index("ix_topics_updated_at", "updated_at"),
        Index("ix_topics_share_status", "share_status"),
        Index("ix_topics_created_by", "created_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))
    share_status: Mapped[str] = mapped_column(String(20), default="private")
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    writings: Mapped[list[Writing]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="Writing.created_at"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class Writing(Base):
    __tablename__ = "writings"
    __table_args__ = (
        Index("ix_writings_topic_id", "topic_id"),
        Index("ix_writings_share_status", "share_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    author: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text)
    share_status: Mapped[str] = mapped_column(String(20), default="private")
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    revision_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    revision_offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="writings")


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_topic_id", "topic_id"),
        Index("ix_comments_writing_id", "writing_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    writing_id: Mapped[int | None] = mapped_column(ForeignKey("writings.id"), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id"), nullable=True)
    author: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    share_status: Mapped[str] = mapped_column(String(20), default="private")
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="comments")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_topic_id", "topic_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    author: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="messages")
