"""API 请求/响应模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EmailIn(BaseModel):
    """调用方提交的单封邮件。"""

    message_id: str = Field(min_length=1, description="邮件唯一标识")
    in_reply_to: str | None = Field(default=None, description="直接回复的 message_id")
    references: list[str] = Field(
        default_factory=list, description="引用链，按时间先后排列"
    )
    sent_at: datetime = Field(description="发送时间（ISO 8601）")
    subject: str = Field(default="", description="主题")


class BatchIn(BaseModel):
    """批量提交请求体。"""

    emails: list[EmailIn] = Field(min_length=1, description="本批次邮件列表")


class ThreadNodeOut(BaseModel):
    message_id: str
    subject: str
    sent_at: datetime
    children: list["ThreadNodeOut"] = []


class DuplicateInfo(BaseModel):
    message_id: str
    occurrences: int


class MissingReference(BaseModel):
    message_id: str = Field(description="发起引用的邮件")
    missing_id: str = Field(description="被引用但批次中不存在的 message_id")
    source: str = Field(description="引用来源字段：in_reply_to 或 references")


class Diagnostics(BaseModel):
    duplicate_ids: list[DuplicateInfo]
    missing_references: list[MissingReference]
    cycles: list[list[str]] = Field(description="引用环，每个环为有序成员列表")
    invalid_ids: list[str] = Field(description="因成环被标记为 invalid 的邮件")


class BatchResult(BaseModel):
    batch_id: int
    created_at: datetime
    email_count: int = Field(description="本批次提交的邮件总数（含重复）")
    threaded_count: int = Field(description="去重后参与穿线的邮件数")
    tree_count: int = Field(description="会话树（根节点）数量")
    invalid_count: int
    trees: list[ThreadNodeOut]
    diagnostics: Diagnostics


class BatchSummary(BaseModel):
    batch_id: int
    created_at: datetime
    email_count: int
    threaded_count: int
    tree_count: int
    invalid_count: int


class EmailRecordOut(BaseModel):
    """持久化的单封邮件归属记录。"""

    message_id: str
    in_reply_to: str | None
    references: list[str]
    sent_at: datetime
    subject: str
    parent_id: str | None
    status: str = Field(description="root / threaded / invalid")
