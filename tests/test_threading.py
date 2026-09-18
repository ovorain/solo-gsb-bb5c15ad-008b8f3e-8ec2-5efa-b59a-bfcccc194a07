"""穿线核心逻辑测试（纯标准库，可直接用 unittest 运行）。"""

import unittest
from datetime import datetime, timezone

from app.threading import EmailRecord, build_threads


def em(mid, reply=None, refs=None, day=1, subject=""):
    return EmailRecord(
        message_id=mid,
        in_reply_to=reply,
        references=refs or [],
        sent_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        subject=subject or mid,
    )


def flatten(node):
    """先序遍历，返回 (message_id, [子节点id...]) 结构便于断言。"""
    return (node.message_id, [flatten(c) for c in node.children])


class ParentResolutionTest(unittest.TestCase):
    def test_in_reply_to_preferred_over_references(self):
        # in_reply_to 有效时优先，即使 references 末位也是已知项
        result = build_threads([
            em("a", day=1),
            em("b", day=2),
            em("c", reply="a", refs=["b", "a"], day=3),
        ])
        res = {r.message_id: r for r in result.resolutions}
        self.assertEqual(res["c"].parent_id, "a")

    def test_fallback_to_last_known_reference(self):
        # in_reply_to 缺失 -> 取 references 中最后一个已知项
        result = build_threads([
            em("a", day=1),
            em("b", reply="a", day=2),
            em("c", refs=["ghost", "a", "b"], day=3),
        ])
        res = {r.message_id: r for r in result.resolutions}
        self.assertEqual(res["c"].parent_id, "b")

    def test_unknown_in_reply_to_falls_back_to_references(self):
        result = build_threads([
            em("a", day=1),
            em("b", reply="ghost", refs=["x", "a"], day=2),
        ])
        res = {r.message_id: r for r in result.resolutions}
        self.assertEqual(res["b"].parent_id, "a")

    def test_broken_link_becomes_root(self):
        result = build_threads([em("lonely", reply="ghost", refs=["g1", "g2"])])
        self.assertEqual([t.message_id for t in result.trees], ["lonely"])
        self.assertEqual(result.resolutions[0].status, "root")

    def test_tree_shape(self):
        result = build_threads([
            em("root", day=1),
            em("child1", reply="root", day=2),
            em("child2", reply="root", day=3),
            em("grand", reply="child1", day=4),
        ])
        self.assertEqual(
            flatten(result.trees[0]),
            ("root", [("child1", [("grand", [])]), ("child2", [])]),
        )


class DiagnosticsTest(unittest.TestCase):
    def test_duplicate_ids_reported_first_occurrence_kept(self):
        result = build_threads([
            em("a", day=1, subject="first"),
            em("a", day=2, subject="second"),
            em("a", day=3, subject="third"),
            em("b", reply="a", day=4),
        ])
        self.assertEqual(
            result.duplicate_ids, [{"message_id": "a", "occurrences": 3}]
        )
        # 仅保留首次出现：主题应为 first
        self.assertEqual(result.trees[0].subject, "first")
        self.assertEqual(len(result.resolutions), 2)

    def test_missing_references_reported_with_source(self):
        result = build_threads([
            em("a", reply="ghost", refs=["g1", "g2"]),
            em("b", refs=["g2"]),
        ])
        self.assertEqual(
            result.missing_references,
            [
                {"message_id": "a", "missing_id": "g1", "source": "references"},
                {"message_id": "a", "missing_id": "g2", "source": "references"},
                {"message_id": "a", "missing_id": "ghost", "source": "in_reply_to"},
                {"message_id": "b", "missing_id": "g2", "source": "references"},
            ],
        )

    def test_missing_reported_even_when_parent_resolved(self):
        # in_reply_to 缺失但 references 成功兜底时，仍应报告缺失项
        result = build_threads([
            em("a"),
            em("b", reply="ghost", refs=["a"]),
        ])
        self.assertEqual(
            result.missing_references,
            [{"message_id": "b", "missing_id": "ghost", "source": "in_reply_to"}],
        )
        res = {r.message_id: r for r in result.resolutions}
        self.assertEqual(res["b"].parent_id, "a")


class CycleTest(unittest.TestCase):
    def test_cycle_all_invalid_no_partial_attach(self):
        result = build_threads([
            em("a", reply="c", day=1),
            em("b", reply="a", day=2),
            em("c", reply="b", day=3),
        ])
        # 父边顺序为 a->c->b->a，规范化后从最小成员 a 开始
        self.assertEqual(result.cycles, [["a", "c", "b"]])
        self.assertEqual(result.invalid_ids, ["a", "b", "c"])
        # 整环摘除：不出现在任何树中
        self.assertEqual(result.trees, [])
        self.assertTrue(
            all(r.status == "invalid" for r in result.resolutions)
        )

    def test_self_loop_is_invalid(self):
        result = build_threads([em("a", reply="a")])
        self.assertEqual(result.cycles, [["a"]])
        self.assertEqual(result.invalid_ids, ["a"])
        self.assertEqual(result.trees, [])

    def test_child_of_cycle_becomes_root(self):
        # d 指向环内节点 c，但自身不在环上 -> 断链 -> 自成根
        result = build_threads([
            em("a", reply="b", day=1),
            em("b", reply="a", day=2),
            em("d", reply="a", day=3),
        ])
        self.assertEqual(result.invalid_ids, ["a", "b"])
        self.assertEqual([t.message_id for t in result.trees], ["d"])
        res = {r.message_id: r for r in result.resolutions}
        self.assertEqual(res["d"].status, "root")
        self.assertIsNone(res["d"].parent_id)

    def test_two_node_cycle_via_references(self):
        result = build_threads([
            em("a", refs=["b"], day=1),
            em("b", refs=["a"], day=2),
        ])
        self.assertEqual(result.cycles, [["a", "b"]])
        self.assertEqual(result.trees, [])


class StableSortTest(unittest.TestCase):
    def test_children_sorted_by_sent_at_then_id(self):
        result = build_threads([
            em("root", day=1),
            em("c2", reply="root", day=3),
            em("c1", reply="root", day=2),
            em("c0", reply="root", day=2),  # 与 c1 同日，按 id 排
        ])
        kids = [c.message_id for c in result.trees[0].children]
        self.assertEqual(kids, ["c0", "c1", "c2"])

    def test_roots_sorted_by_sent_at_then_id(self):
        result = build_threads([
            em("z", day=3),
            em("b", day=2),
            em("a", day=2),
        ])
        self.assertEqual([t.message_id for t in result.trees], ["a", "b", "z"])

    def test_naive_and_aware_datetimes_mixed(self):
        naive = EmailRecord("n", None, [], datetime(2026, 1, 1, 12), "n")
        aware = EmailRecord(
            "w", None, [], datetime(2026, 1, 1, 1, tzinfo=timezone.utc), "w"
        )
        result = build_threads([naive, aware])
        self.assertEqual([t.message_id for t in result.trees], ["w", "n"])


if __name__ == "__main__":
    unittest.main()
