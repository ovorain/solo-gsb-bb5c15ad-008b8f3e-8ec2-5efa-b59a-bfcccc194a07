"""API 端到端测试。

本地无 fastapi 时自动跳过；在 Docker 镜像或装好依赖的环境中运行。
"""

import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi 未安装，跳过 API 测试")
class ApiTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        os.environ["DATABASE_PATH"] = self.db_path

        # 环境变量在模块导入时读取，需重新导入
        import importlib

        import app.main

        importlib.reload(app.main)
        self.client = TestClient(app.main.app)
        self.addCleanup(os.environ.pop, "DATABASE_PATH", None)
        self.addCleanup(os.unlink, self.db_path)

    def test_full_flow(self):
        payload = {
            "emails": [
                {
                    "message_id": "m1",
                    "in_reply_to": None,
                    "references": [],
                    "sent_at": "2026-01-01T09:00:00Z",
                    "subject": "周报",
                },
                {
                    "message_id": "m2",
                    "in_reply_to": "m1",
                    "references": ["m1"],
                    "sent_at": "2026-01-01T10:00:00Z",
                    "subject": "Re: 周报",
                },
                {
                    "message_id": "m3",
                    "in_reply_to": "ghost",
                    "references": ["ghost", "m2"],
                    "sent_at": "2026-01-01T11:00:00Z",
                    "subject": "Re: Re: 周报",
                },
                {"message_id": "m2", "sent_at": "2026-01-01T10:00:00Z"},
                {
                    "message_id": "x",
                    "in_reply_to": "y",
                    "sent_at": "2026-01-02T09:00:00Z",
                },
                {
                    "message_id": "y",
                    "in_reply_to": "x",
                    "sent_at": "2026-01-02T10:00:00Z",
                },
            ]
        }
        resp = self.client.post("/api/v1/batches", json=payload)
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()

        self.assertEqual(body["batch_id"], 1)
        self.assertEqual(body["email_count"], 6)
        self.assertEqual(body["threaded_count"], 5)

        # m3 的 in_reply_to 缺失，兜底 references 末位已知项 m2
        tree = body["trees"][0]
        self.assertEqual(tree["message_id"], "m1")
        self.assertEqual(tree["children"][0]["message_id"], "m2")
        self.assertEqual(
            tree["children"][0]["children"][0]["message_id"], "m3"
        )

        diag = body["diagnostics"]
        self.assertEqual(
            diag["duplicate_ids"], [{"message_id": "m2", "occurrences": 2}]
        )
        self.assertIn(
            {"message_id": "m3", "missing_id": "ghost", "source": "in_reply_to"},
            diag["missing_references"],
        )
        self.assertEqual(diag["cycles"], [["x", "y"]])
        self.assertEqual(diag["invalid_ids"], ["x", "y"])
        # 整环摘除：x/y 不出现在任何树中
        self.assertEqual(len(body["trees"]), 1)

        # 查询接口
        resp = self.client.get("/api/v1/batches")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

        resp = self.client.get(f"/api/v1/batches/{body['batch_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["batch_id"], body["batch_id"])

        resp = self.client.get(f"/api/v1/batches/{body['batch_id']}/emails")
        self.assertEqual(resp.status_code, 200)
        rows = {r["message_id"]: r for r in resp.json()}
        self.assertEqual(rows["m3"]["parent_id"], "m2")
        self.assertEqual(rows["x"]["status"], "invalid")
        self.assertEqual(len(rows), 5)  # 重复 m2 仅落库一次

        resp = self.client.get("/api/v1/batches/999")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.get("/health")
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_validation_error(self):
        resp = self.client.post("/api/v1/batches", json={"emails": []})
        self.assertEqual(resp.status_code, 422)
        resp = self.client.post(
            "/api/v1/batches",
            json={"emails": [{"message_id": "", "sent_at": "2026-01-01T00:00:00Z"}]},
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
