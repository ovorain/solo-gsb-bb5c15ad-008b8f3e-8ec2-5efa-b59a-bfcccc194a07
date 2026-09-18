"""SQLite 持久化层测试（纯标准库）。"""

import unittest

from app.db import Database


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.init_schema()

    def tearDown(self):
        self.db.close()

    def _save(self, result_extra=None):
        result_json = {
            "batch_id": 0,
            "created_at": "",
            "email_count": 2,
            "threaded_count": 2,
            "tree_count": 1,
            "invalid_count": 0,
            "trees": [],
            "diagnostics": {
                "duplicate_ids": [],
                "missing_references": [],
                "cycles": [],
                "invalid_ids": [],
            },
        }
        result_json.update(result_extra or {})
        return self.db.save_batch(
            email_rows=[
                {
                    "message_id": "a",
                    "in_reply_to": None,
                    "references": [],
                    "sent_at": "2026-01-01T00:00:00+00:00",
                    "subject": "a",
                    "parent_id": None,
                    "status": "root",
                },
                {
                    "message_id": "b",
                    "in_reply_to": "a",
                    "references": ["a"],
                    "sent_at": "2026-01-02T00:00:00+00:00",
                    "subject": "b",
                    "parent_id": "a",
                    "status": "threaded",
                },
            ],
            result_json=result_json,
            email_count=2,
            threaded_count=2,
            tree_count=1,
            invalid_count=0,
        )

    def test_save_and_get_roundtrip(self):
        stored = self._save()
        self.assertEqual(stored["batch_id"], 1)
        self.assertTrue(stored["created_at"])

        fetched = self.db.get_batch_result(1)
        self.assertEqual(fetched, stored)

    def test_get_missing_batch_returns_none(self):
        self.assertIsNone(self.db.get_batch_result(999))

    def test_list_batches(self):
        self._save()
        self._save()
        batches = self.db.list_batches()
        self.assertEqual([b["batch_id"] for b in batches], [1, 2])
        self.assertEqual(batches[0]["email_count"], 2)

    def test_list_emails(self):
        self._save()
        rows = self.db.list_emails(1)
        self.assertEqual([r["message_id"] for r in rows], ["a", "b"])
        self.assertEqual(rows[1]["parent_id"], "a")
        self.assertEqual(rows[1]["references"], ["a"])
        self.assertIsNone(self.db.list_emails(999))

    def test_duplicate_message_id_rejected_by_constraint(self):
        self._save()
        with self.assertRaises(Exception):
            self.db.save_batch(
                email_rows=[
                    {
                        "message_id": "x",
                        "in_reply_to": None,
                        "references": [],
                        "sent_at": "2026-01-01T00:00:00+00:00",
                        "subject": "x",
                        "parent_id": None,
                        "status": "root",
                    }
                ]
                * 2,  # 同批次重复 message_id
                result_json={},
                email_count=2,
                threaded_count=2,
                tree_count=1,
                invalid_count=0,
            )


if __name__ == "__main__":
    unittest.main()
