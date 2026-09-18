"""FastAPI 入口：邮件归档整理服务。

启动时自动建表（见 lifespan）。配置通过环境变量注入：

- ``DATABASE_PATH``：SQLite 文件路径，默认 ``./mail_archive.db``
  （Docker 镜像中默认为 ``/data/mail_archive.db``）。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status

from .db import Database
from .models import (
    BatchIn,
    BatchResult,
    BatchSummary,
    EmailRecordOut,
    ThreadNodeOut,
)
from .threading import EmailRecord, ThreadNode, build_threads

DATABASE_PATH = os.environ.get("DATABASE_PATH", "./mail_archive.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(DATABASE_PATH)
    db.init_schema()  # 依赖的表结构随启动创建
    app.state.db = db
    yield
    db.close()


app = FastAPI(
    title="Mail Archive Organizer",
    version="1.0.0",
    description="批量提交邮件头信息，按引用关系整理为会话树，并输出重复 ID、缺失引用与引用环诊断。",
    lifespan=lifespan,
)


def get_db(request: Request) -> Database:
    return request.app.state.db


def _node_out(node: ThreadNode) -> ThreadNodeOut:
    return ThreadNodeOut(
        message_id=node.message_id,
        subject=node.subject,
        sent_at=node.sent_at,
        children=[_node_out(child) for child in node.children],
    )


@app.get("/", summary="服务信息")
def root() -> dict:
    return {
        "service": "mail-archive-organizer",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1/batches",
    }


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/batches",
    response_model=BatchResult,
    status_code=status.HTTP_201_CREATED,
    summary="批量提交邮件并返回会话树与诊断",
)
def create_batch(payload: BatchIn, request: Request) -> BatchResult:
    records = [
        EmailRecord(
            message_id=e.message_id,
            in_reply_to=e.in_reply_to,
            references=e.references,
            sent_at=e.sent_at,
            subject=e.subject,
        )
        for e in payload.emails
    ]
    result = build_threads(records)

    resolutions = {r.message_id: r for r in result.resolutions}
    # 与穿线逻辑一致：重复 message_id 仅保留首次出现，避免主键冲突
    email_rows = []
    seen: set[str] = set()
    for e in payload.emails:
        if e.message_id in seen:
            continue
        seen.add(e.message_id)
        email_rows.append(
            {
                "message_id": e.message_id,
                "in_reply_to": e.in_reply_to,
                "references": e.references,
                "sent_at": e.sent_at.isoformat(),
                "subject": e.subject,
                "parent_id": resolutions[e.message_id].parent_id,
                "status": resolutions[e.message_id].status,
            }
        )

    result_json = {
        "batch_id": 0,  # 由持久化层覆盖
        "created_at": "",  # 由持久化层覆盖
        "email_count": len(payload.emails),
        "threaded_count": len(result.resolutions),
        "tree_count": len(result.trees),
        "invalid_count": len(result.invalid_ids),
        "trees": [
            _node_out(t).model_dump(mode="json") for t in result.trees
        ],
        "diagnostics": {
            "duplicate_ids": result.duplicate_ids,
            "missing_references": result.missing_references,
            "cycles": result.cycles,
            "invalid_ids": result.invalid_ids,
        },
    }

    db = get_db(request)
    stored = db.save_batch(
        email_rows=email_rows,
        result_json=result_json,
        email_count=len(payload.emails),
        threaded_count=len(result.resolutions),
        tree_count=len(result.trees),
        invalid_count=len(result.invalid_ids),
    )
    return BatchResult.model_validate(stored)


@app.get(
    "/api/v1/batches",
    response_model=list[BatchSummary],
    summary="列出全部批次摘要",
)
def list_batches(request: Request) -> list[dict]:
    return get_db(request).list_batches()


@app.get(
    "/api/v1/batches/{batch_id}",
    response_model=BatchResult,
    summary="查询某批次的完整结果",
)
def get_batch(batch_id: int, request: Request) -> dict:
    result = get_db(request).get_batch_result(batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
    return result


@app.get(
    "/api/v1/batches/{batch_id}/emails",
    response_model=list[EmailRecordOut],
    summary="查询某批次每封邮件的归属结果",
)
def get_batch_emails(batch_id: int, request: Request) -> list[dict]:
    rows = get_db(request).list_emails(batch_id)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
    return rows
