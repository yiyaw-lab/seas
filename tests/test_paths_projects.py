"""The projects log must be volume-overridable, like every other Argo store.

PROJECTS_LOG used to be hardcoded to the repo checkout (DATA/argo_projects.json),
unlike the sibling chat/self/taste stores which read an ARGO_*_PATH env override so
they can live on the Railway persistent volume. The webhook appends a new project
every time it proposes one; on the ephemeral container disk that log is wiped on
every redeploy, so the max+1 id counter always reads an empty file and resets to
P-001 -- the "every project is indexed 001" bug. This locks in the env override.

Pure + hermetic. Reloads argo_paths under a patched env and restores it after.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import importlib
import os
import unittest
from unittest import mock

import argo_paths


class ProjectsLogPathTest(unittest.TestCase):
    def _reload_clean(self):
        env = dict(os.environ)
        env.pop("ARGO_PROJECTS_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(argo_paths)

    def setUp(self):
        # leave argo_paths back at its env-default state for the rest of the suite
        self.addCleanup(self._reload_clean)

    def test_honors_volume_override(self):
        with mock.patch.dict(os.environ,
                             {"ARGO_PROJECTS_PATH": "/vol/data/argo_projects.json"}):
            importlib.reload(argo_paths)
            self.assertEqual(str(argo_paths.PROJECTS_LOG),
                             "/vol/data/argo_projects.json")

    def test_project_module_sources_path_from_argo_paths(self):
        import argo_project
        self.assertEqual(argo_project.PROJECTS_LOG, argo_paths.PROJECTS_LOG)


if __name__ == "__main__":
    unittest.main()
