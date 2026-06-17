"""The pending-heal store must be volume-overridable, like every other Argo store.

PENDING_HEAL_PATH used to be hardcoded to the repo checkout (ROOT/data), unlike the
sibling PENDING_EVOLVE_PATH and all the other stores, which read an ARGO_*_PATH env
override so they can live on the Railway persistent volume. The CONFIRM/FIX gate
stages a heal action and then waits (often hours) for the user's reply -- a window
that can span a redeploy, which on the ephemeral checkout silently drops the staged
fix so FIX finds nothing. This locks in the env override + the argo_mcp_server wiring.

Pure + hermetic. Reloads argo_paths under a patched env and restores it after.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import importlib
import os
import unittest
from unittest import mock

import argo_paths


class PendingHealPathTest(unittest.TestCase):
    def _reload_clean(self):
        env = dict(os.environ)
        env.pop("ARGO_PENDING_HEAL_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(argo_paths)

    def setUp(self):
        # leave argo_paths back at its env-default state for the rest of the suite
        self.addCleanup(self._reload_clean)

    def test_honors_volume_override(self):
        with mock.patch.dict(os.environ,
                             {"ARGO_PENDING_HEAL_PATH": "/vol/data/argo_pending_heal.json"}):
            importlib.reload(argo_paths)
            self.assertEqual(str(argo_paths.PENDING_HEAL_PATH),
                             "/vol/data/argo_pending_heal.json")

    def test_mcp_server_sources_path_from_argo_paths(self):
        import argo_mcp_server
        self.assertEqual(argo_mcp_server.PENDING_HEAL_PATH, argo_paths.PENDING_HEAL_PATH)


if __name__ == "__main__":
    unittest.main()
