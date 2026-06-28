"""Task 4 (roadmap item 5, part 2): contract semver + consumer-edge ripple. A contract's
`version` plus its consumer edges make a bump's blast radius EXPLICIT and ROUTED -- the
tasks/agents that import the seam, not "consumers re-verify" in the abstract. Consumers are
the contract's explicit `consumers` UNION every task depending on its owner_task. PURE.
"""

import io
import unittest
import zipfile

import seasar_compile as sc
from seasar_verify import verify_order


def _order():
    # T1 owns Api; T2 depends on T1 (derived consumer); T3 declared explicitly; T4 unrelated.
    return {"title": "X",
            "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.py"]},
                      {"id": "T2", "wave": 2, "depends_on": ["T1"], "files": ["src/b.py"]},
                      {"id": "T3", "wave": 2, "files": ["src/c.py"]},
                      {"id": "T4", "wave": 1, "files": ["src/d.py"]}],
            "contracts": [{"name": "Api", "owner_task": "T1", "source_lang": "python",
                           "source": "x = 1", "source_path": "src/api.py",
                           "consumers": ["T3"], "behavior": {"units": "cents"}}],
            "work_orders": [{"agent": "agent-a", "task_ids": ["T1"]},
                            {"agent": "agent-b", "task_ids": ["T2", "T3"]},
                            {"agent": "agent-c", "task_ids": ["T4"]}]}


def _bundle_file(order, suffix):
    with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode()


class ConsumerEdgeTest(unittest.TestCase):
    def test_derived_and_declared_union_excludes_owner(self):
        order = _order()
        sc._normalize_order(order)
        ids, agents = sc._contract_consumers(order, order["contracts"][0])
        self.assertEqual(ids, ["T2", "T3"])           # T2 derived, T3 declared; T1 owner excluded
        self.assertEqual(agents, ["agent-b"])         # both map to agent-b
        self.assertNotIn("T4", ids)                   # unrelated task is not a consumer

    def test_owner_never_self_consumes(self):
        order = {"tasks": [{"id": "T1", "depends_on": ["T1"]}],
                 "contracts": [{"name": "C", "owner_task": "T1", "consumers": ["T1"]}]}
        sc._normalize_order(order)
        ids, _ = sc._contract_consumers(order, order["contracts"][0])
        self.assertEqual(ids, [])


class EmitTest(unittest.TestCase):
    def test_ripple_in_contract_changes(self):
        order = _order()
        sc._normalize_order(order)
        md = sc._md_contract_changes(order)
        self.assertIn("v1.0.0", md)
        self.assertIn("on a version bump, re-verify: agent-b", md)
        self.assertIn("tasks T2, T3", md)

    def test_leaf_seam_labeled(self):
        order = {"tasks": [{"id": "T1"}],
                 "contracts": [{"name": "Solo", "owner_task": "T1"}]}
        sc._normalize_order(order)
        self.assertIn("no downstream consumers (leaf seam)", sc._md_contract_changes(order))

    def test_consumer_agent_packet_lists_consumed_contract(self):
        order = _order()
        sc._normalize_order(order)
        # agent-b owns T2/T3 which consume Api -> its work order must say so.
        packet = _bundle_file(order, "work-orders/agent-b.md")
        self.assertIn("Contracts you consume", packet)
        self.assertIn("`Api` v1.0.0", packet)
        # agent-c (T4) consumes nothing -> no consume section.
        packet_c = _bundle_file(order, "work-orders/agent-c.md")
        self.assertNotIn("Contracts you consume", packet_c)

    def test_consumer_agent_packet_handles_raw_int_ids(self):
        order = {"tasks": [{"id": 1, "files": ["src/api.py"]},
                           {"id": 2, "files": ["src/b.py"]}],
                 "contracts": [{"name": "Api", "owner_task": 1, "source_path": "src/api.py",
                                "consumers": [2]}]}
        packet = sc._md_work_order({"agent": "agent-b", "task_ids": [2]}, order)
        self.assertIn("Contracts you consume", packet)
        self.assertIn("`Api`", packet)


class VerifyTest(unittest.TestCase):
    def test_dangling_consumer_warns(self):
        order = {"tasks": [{"id": "T1"}],
                 "contracts": [{"name": "C", "owner_task": "T1", "source": "x=1",
                                "source_path": "c.py", "consumers": ["T9"]}]}
        res = verify_order(order)
        chk = next(c for c in res["checks"] if c["name"] == "consumers_are_tasks")
        self.assertFalse(chk["ok"])
        self.assertIn("C->T9", chk["detail"])

    def test_real_consumer_passes(self):
        order = {"tasks": [{"id": "T1"}, {"id": "T2"}],
                 "contracts": [{"name": "C", "owner_task": "T1", "source": "x=1",
                                "source_path": "c.py", "consumers": ["T2"]}]}
        res = verify_order(order)
        chk = next(c for c in res["checks"] if c["name"] == "consumers_are_tasks")
        self.assertTrue(chk["ok"])

    def test_int_consumers_match_raw_int_task_ids(self):
        order = {"tasks": [{"id": 1}, {"id": 2}],
                 "contracts": [{"name": "C", "owner_task": 1, "source": "x=1",
                                "source_path": "c.py", "consumers": ["2"]}]}
        res = verify_order(order)
        chk = next(c for c in res["checks"] if c["name"] == "consumers_are_tasks")
        self.assertTrue(chk["ok"])

    def test_int_dangling_consumer_warns(self):
        order = {"tasks": [{"id": 1}],
                 "contracts": [{"name": "C", "owner_task": 1, "source": "x=1",
                                "source_path": "c.py", "consumers": [9]}]}
        res = verify_order(order)
        chk = next(c for c in res["checks"] if c["name"] == "consumers_are_tasks")
        self.assertFalse(chk["ok"])
        self.assertIn("C->9", chk["detail"])


if __name__ == "__main__":
    unittest.main()
