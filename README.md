# Mail Archive Organizer（邮件归档整理服务）

基于 FastAPI 的邮件归档整理服务：调用方批量提交邮件头信息，服务按引用关系
将邮件整理为会话树，并输出重复 ID、缺失引用、引用环等诊断。批次与结果
持久化到 SQLite，可随时查询。

## 快速启动（单条命令）

```bash
docker compose up --build
```

服务地址：**http://localhost:8000**

- 交互式 API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

依赖安装（`pip install -r requirements.txt`）在镜像构建时完成，SQLite
建表在服务启动时自动完成，无需任何手工初始化。

## 配置

通过环境变量配置（`docker-compose.yml` 中已给出默认值）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | `/data/mail_archive.db`（本地运行为 `./mail_archive.db`） | SQLite 数据库文件路径 |

Compose 通过命名卷 `mail-archive-data` 持久化 `/data`，容器重建后数据保留。

本地开发（不用 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 穿线规则

1. **父节点判定**：优先采用有效的 `in_reply_to`（指向本批次内已知的
   `message_id`）；否则采用 `references` 列表中**最后一个**已知项。
2. **断链**：无任何有效父引用的邮件自成根节点。
3. **引用环**：检测到环时，**整环标记为 `invalid` 并整体摘除，不做部分
   挂接**；环外指向环内节点的邮件视为断链，成为根节点。
4. **重复 ID**：同一 `message_id` 仅首次出现参与穿线，其余记入诊断。
5. **稳定排序**：根节点之间、兄弟节点之间均按 `(sent_at, message_id)`
   排序，同一输入永远得到同一输出（naive 时间按 UTC 处理）。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/batches` | 批量提交邮件，返回会话树与诊断（201） |
| `GET` | `/api/v1/batches` | 列出全部批次摘要 |
| `GET` | `/api/v1/batches/{batch_id}` | 查询某批次的完整结果 |
| `GET` | `/api/v1/batches/{batch_id}/emails` | 查询某批次每封邮件的归属（parent_id / status） |
| `GET` | `/health` | 健康检查 |

### 提交示例

```bash
curl -X POST http://localhost:8000/api/v1/batches \
  -H 'Content-Type: application/json' \
  -d '{
    "emails": [
      {"message_id": "m1", "in_reply_to": null, "references": [],
       "sent_at": "2026-01-01T09:00:00Z", "subject": "周报"},
      {"message_id": "m2", "in_reply_to": "m1", "references": ["m1"],
       "sent_at": "2026-01-01T10:00:00Z", "subject": "Re: 周报"},
      {"message_id": "m3", "in_reply_to": "ghost", "references": ["ghost", "m2"],
       "sent_at": "2026-01-01T11:00:00Z", "subject": "Re: Re: 周报"}
    ]
  }'
```

响应（节选）：`m3` 的 `in_reply_to` 指向批次外邮件，按规则兜底到
`references` 末位已知项 `m2`；`ghost` 记入缺失引用诊断。

```json
{
  "batch_id": 1,
  "email_count": 3,
  "tree_count": 1,
  "invalid_count": 0,
  "trees": [
    {"message_id": "m1", "subject": "周报", "children": [
      {"message_id": "m2", "children": [
        {"message_id": "m3", "children": []}
      ]}
    ]}
  ],
  "diagnostics": {
    "duplicate_ids": [],
    "missing_references": [
      {"message_id": "m3", "missing_id": "ghost", "source": "in_reply_to"}
    ],
    "cycles": [],
    "invalid_ids": []
  }
}
```

## 测试

```bash
# 核心逻辑与持久化层（纯标准库，无需安装依赖）
python3 -m unittest discover -s tests

# 完整 API 测试需安装依赖后运行（镜像内已含）
pip install -r requirements.txt
python3 -m unittest discover -s tests
```

## 项目结构

```
app/
  main.py       FastAPI 入口与路由，启动时建表
  threading.py  穿线核心逻辑（纯标准库，可独立测试）
  models.py     请求/响应模型
  db.py         SQLite 持久化层
tests/          单元测试与 API 端到端测试
Dockerfile      镜像构建（依赖随构建安装）
docker-compose.yml  单命令启动，卷持久化数据
```
