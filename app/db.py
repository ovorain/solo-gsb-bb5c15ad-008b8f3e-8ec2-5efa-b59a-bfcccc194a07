"""SQLite 持久化层（标准库 sqlite3）。

表结构随服务启动自动创建（见 ``init_db``）。批次结果以 JSON 整体
保存，同时按邮件落一张明细表，便于按批次查询归属结果。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    email_count    INTEGER NOT NULL,
    threaded_count INTEGER NOT NULL,
    tree_count     INTEGER NOT NULL,
    invalid_count  INTEGER NOT NULL,
    result_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    message_id      TEXT NOT NULL,
    in_reply_to     TEXT,
    references_json TEXT NOT NULL DEFAULT '[]',
    sent_at         TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    parent_id       TEXT,
    status          TEXT NOT NULL,
    UNIQUE (batch_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_batch ON emails(batch_id);
"""


class Database:
    """线程安全的轻量 SQLite 封装。"""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 写入 ----

    def save_batch(
        self,
        *,
        email_rows: list[dict],
        result_json: dict,
        email_count: int,
        threaded_count: int,
        tree_count: int,
        invalid_count: int,
    ) -> dict:
        """保存批次结果与邮件明细。

        ``result_json`` 中的 ``batch_id`` / ``created_at`` 由本方法
        以权威值覆盖，返回最终落库的结果字典。
        """
        created_at = datetime.now(timezone.utc)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO batches
                    (created_at, email_count, threaded_count,
                     tree_count, invalid_count, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at.isoformat(),
                    email_count,
                    threaded_count,
                    tree_count,
                    invalid_count,
                    json.dumps(result_json, ensure_ascii=False),
                ),
            )
            batch_id = int(cur.lastrowid)
            result_json["batch_id"] = batch_id
            result_json["created_at"] = created_at.isoformat()
            self._conn.execute(
                "UPDATE batches SET result_json = ? WHERE id = ?",
                (json.dumps(result_json, ensure_ascii=False), batch_id),
            )
            self._conn.executemany(
                """
                INSERT INTO emails
                    (batch_id, message_id, in_reply_to, references_json,
                     sent_at, subject, parent_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        row["message_id"],
                        row["in_reply_to"],
                        json.dumps(row["references"], ensure_ascii=False),
                        row["sent_at"],
                        row["subject"],
                        row["parent_id"],
                        row["status"],
                    )
                    for row in email_rows
                ],
            )
        return result_json

    # ---- 查询 ----

    def get_batch_result(self, batch_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def list_batches(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id AS batch_id, created_at, email_count,
                       threaded_count, tree_count, invalid_count
                FROM batches ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_emails(self, batch_id: int) -> list[dict] | None:
        """批次不存在时返回 None，存在则返回邮件明细列表。"""
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if not exists:
                return None
            rows = self._conn.execute(
                """
                SELECT message_id, in_reply_to, references_json,
                       sent_at, subject, parent_id, status
                FROM emails WHERE batch_id = ? ORDER BY id
                """,
                (batch_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["references"] = json.loads(item.pop("references_json"))
            result.append(item)
        return result
