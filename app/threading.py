"""邮件会话穿线（threading）核心逻辑。

纯标准库实现，不依赖 Web 框架，便于独立测试。

父节点判定规则：
1. 优先采用有效的 ``in_reply_to``（指向本批次内已知的 message_id）；
2. 否则采用 ``references`` 列表中**最后一个**已知项；
3. 断链邮件（无有效父引用）自成根节点；
4. 检测到引用环时，整环标记为 invalid 并整体摘除，不做部分挂接；
   环外指向环内节点的邮件视为断链，成为根节点。

输出按 ``(sent_at, message_id)`` 做稳定排序：根节点之间、同一节点的
子节点之间均遵循该顺序，保证同一输入永远得到同一结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

STATUS_ROOT = "root"
STATUS_THREADED = "threaded"
STATUS_INVALID = "invalid"


@dataclass
class EmailRecord:
    """穿线计算所需的最小邮件字段。"""

    message_id: str
    in_reply_to: str | None
    references: list[str]
    sent_at: datetime
    subject: str = ""


@dataclass
class ThreadNode:
    message_id: str
    subject: str
    sent_at: datetime
    children: list["ThreadNode"] = field(default_factory=list)


@dataclass
class Resolution:
    """单封邮件的归属结果，用于持久化。"""

    message_id: str
    parent_id: str | None
    status: str  # root / threaded / invalid


@dataclass
class ThreadResult:
    trees: list[ThreadNode]
    resolutions: list[Resolution]
    duplicate_ids: list[dict]  # [{"message_id": ..., "occurrences": n}]
    missing_references: list[dict]  # [{"message_id", "missing_id", "source"}]
    cycles: list[list[str]]
    invalid_ids: list[str]


def _normalize_dt(dt: datetime) -> datetime:
    """naive 时间按 UTC 处理，避免与 tz-aware 时间比较时报错。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _sort_key(message_id: str, sent_at: datetime) -> tuple:
    return (_normalize_dt(sent_at), message_id)


def _resolve_parent(record: EmailRecord, known: set[str]) -> str | None:
    """按“in_reply_to 优先，references 末位已知项兜底”解析父节点。"""
    if record.in_reply_to and record.in_reply_to in known:
        return record.in_reply_to
    for ref in reversed(record.references):
        if ref in known:
            return ref
    return None


def _find_cycles(parent: dict[str, str | None]) -> list[list[str]]:
    """在函数图（每节点至多一条出边）上检测全部环。

    三色标记 DFS。返回的每个环按字典序最小成员开头做规范化，
    环列表本身按首成员排序，保证输出确定。
    """
    white, gray, black = 0, 1, 2
    color = {node: white for node in parent}
    cycles: list[list[str]] = []

    for start in parent:
        if color[start] != white:
            continue
        path: list[str] = []
        node: str | None = start
        while node is not None and color[node] == white:
            color[node] = gray
            path.append(node)
            node = parent[node]
        if node is not None and color[node] == gray:
            cycle = path[path.index(node):]
            # 规范化：从字典序最小成员开始，便于稳定输出与比较
            pivot = cycle.index(min(cycle))
            cycles.append(cycle[pivot:] + cycle[:pivot])
        for item in path:
            color[item] = black

    cycles.sort(key=lambda c: c[0])
    return cycles


def build_threads(emails: list[EmailRecord]) -> ThreadResult:
    """对一批邮件执行穿线，返回会话森林与诊断信息。"""
    # 1. 去重：同一 message_id 仅保留首次出现，其余记入诊断
    unique: dict[str, EmailRecord] = {}
    duplicates: dict[str, int] = {}
    for record in emails:
        if record.message_id in unique:
            duplicates[record.message_id] = duplicates.get(record.message_id, 1) + 1
            continue
        unique[record.message_id] = record

    known = set(unique)

    duplicate_ids = [
        {"message_id": mid, "occurrences": count}
        for mid, count in sorted(duplicates.items())
    ]

    # 2. 缺失引用诊断（在去重后的邮件集合上统计）
    missing: set[tuple[str, str, str]] = set()
    for record in unique.values():
        if record.in_reply_to and record.in_reply_to not in known:
            missing.add((record.message_id, record.in_reply_to, "in_reply_to"))
        for ref in record.references:
            if ref not in known:
                missing.add((record.message_id, ref, "references"))
    missing_references = [
        {"message_id": mid, "missing_id": ref, "source": source}
        for mid, ref, source in sorted(missing)
    ]

    # 3. 解析父节点
    parent: dict[str, str | None] = {
        mid: _resolve_parent(record, known) for mid, record in unique.items()
    }

    # 4. 环检测：整环标记 invalid 并整体摘除，不做部分挂接
    cycles = _find_cycles(parent)
    invalid: set[str] = {mid for cycle in cycles for mid in cycle}
    invalid_ids = sorted(invalid)

    # 5. 环外节点若父边指向环内，视为断链 -> 成为根节点
    for mid, pid in parent.items():
        if mid not in invalid and pid in invalid:
            parent[mid] = None

    # 6. 构建森林（排除 invalid 节点）
    children: dict[str, list[str]] = {mid: [] for mid in known - invalid}
    roots: list[str] = []
    for mid in children:
        pid = parent[mid]
        if pid is None or pid in invalid:
            roots.append(mid)
        else:
            children[pid].append(mid)

    def sort_ids(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda m: _sort_key(m, unique[m].sent_at))

    def to_node(mid: str) -> ThreadNode:
        record = unique[mid]
        return ThreadNode(
            message_id=mid,
            subject=record.subject,
            sent_at=record.sent_at,
            children=[to_node(c) for c in sort_ids(children[mid])],
        )

    trees = [to_node(mid) for mid in sort_ids(roots)]

    # 7. 每封邮件的归属结果（用于持久化）
    resolutions = [
        Resolution(
            message_id=mid,
            parent_id=None if mid in invalid else parent[mid],
            status=(
                STATUS_INVALID
                if mid in invalid
                else (STATUS_ROOT if parent[mid] is None else STATUS_THREADED)
            ),
        )
        for mid in unique
    ]

    return ThreadResult(
        trees=trees,
        resolutions=resolutions,
        duplicate_ids=duplicate_ids,
        missing_references=missing_references,
        cycles=cycles,
        invalid_ids=invalid_ids,
    )
