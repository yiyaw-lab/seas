"""Task 3 (roadmap item 5, part 1): behavioral-contract block + language-neutral interface
IR. A contract's `source` pins the SHAPE; `behavior` (ordering/idempotency/errors/
pagination/units) + `interface` pin the SEMANTICS -- the seam where two agents agree on
types and silently diverge. PURE per CLAUDE.md: orders inline, no network/LLM/data files.
"""

import io
import json
import unittest
import zipfile

import seasar_compile as sc
from seasar_verify import verify_order


def _sourced(behavior=None, interface=None):
    c = {"name": "Api", "owner_task": "T1", "source_lang": "python",
         "source": "x = 1", "source_path": "src/api.py"}
    if behavior is not None:
        c["behavior"] = behavior
    if interface is not None:
        c["interface"] = interface
    return {"title": "X", "tasks": [{"id": "T1", "wave": 1, "files": ["src/api.py"]}],
            "contracts": [c]}


def _warn(res, name):
    return next(c for c in res["checks"] if c["name"] == name)


class NormalizeTest(unittest.TestCase):
    def test_behavior_keeps_recognized_drops_junk(self):
        order = _sourced(behavior={"ordering": "by id", "bogus": "x", "units": ""})
        sc._normalize_order(order)
        self.assertEqual(order["contracts"][0]["behavior"], {"ordering": "by id"})

    def test_behavior_non_dict_becomes_empty(self):
        order = _sourced(behavior="idempotent maybe")
        sc._normalize_order(order)
        self.assertEqual(order["contracts"][0]["behavior"], {})

    def test_interface_coerces_ops_drops_nameless(self):
        order = _sourced(interface=[
            {"op": "get", "params": [{"name": "id", "type": "str"}, {"type": "x"}],
             "returns": "Item", "errors": ["NotFound", ""]},
            {"params": []},      # no op name -> dropped
            "garbage",
        ])
        sc._normalize_order(order)
        iface = order["contracts"][0]["interface"]
        self.assertEqual(len(iface), 1)
        self.assertEqual(iface[0]["op"], "get")
        self.assertEqual(iface[0]["params"], [{"name": "id", "type": "str"}])
        self.assertEqual(iface[0]["errors"], ["NotFound"])


class VerifyTest(unittest.TestCase):
    def test_behavior_warn_fires_when_absent(self):
        res = verify_order(_sourced())          # sourced contract, no behavior/interface
        self.assertFalse(_warn(res, "contracts_specify_behavior")["ok"])

    def test_behavior_warn_passes_with_block(self):
        res = verify_order(_sourced(behavior={"ordering": "stable, by created_at desc"}))
        self.assertTrue(_warn(res, "contracts_specify_behavior")["ok"])

    def test_behavior_warn_ignores_junk_only_block(self):
        res = verify_order(_sourced(behavior={"notes": "idempotent maybe", "bogus": "x"}))
        self.assertFalse(_warn(res, "contracts_specify_behavior")["ok"])

    def test_behavior_warn_passes_with_interface(self):
        res = verify_order(_sourced(interface=[{"op": "list", "returns": "Item[]"}]))
        self.assertTrue(_warn(res, "contracts_specify_behavior")["ok"])

    def test_behavior_warn_folds_into_self_check(self):
        # A behaviorally-specified contract scores higher on the independent (self_check)
        # axis than a type-shape-only one.
        without = verify_order(_sourced())["independent_executability"]
        withb = verify_order(_sourced(behavior={"units": "cents"}))["independent_executability"]
        self.assertGreater(withb, without)


class EmitTest(unittest.TestCase):
    def test_md_renders_behavior_and_interface(self):
        c = {"name": "Api", "behavior": {"idempotency": "PUT is idempotent"},
             "interface": [{"op": "get", "params": [{"name": "id", "type": "str"}],
                            "returns": "Item", "errors": ["NotFound"]}]}
        md = sc._md_contract(c)
        self.assertIn("## Behavior", md)
        self.assertIn("PUT is idempotent", md)
        self.assertIn("## Interface", md)
        self.assertIn("`get(id: str)` -> Item", md)
        self.assertIn("NotFound", md)

    def test_ir_file_emitted_into_bundle(self):
        order = _sourced(behavior={"ordering": "fifo"},
                         interface=[{"op": "push", "params": [{"name": "m", "type": "Msg"}]}])
        sc._normalize_order(order)
        with zipfile.ZipFile(io.BytesIO(sc.build_bundle(order))) as z:
            name = next(n for n in z.namelist() if n.endswith("contracts/api.contract.json"))
            ir = json.loads(z.read(name))
        self.assertEqual(ir["behavior"], {"ordering": "fifo"})
        self.assertEqual(ir["interface"][0]["op"], "push")
        self.assertEqual(ir["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
