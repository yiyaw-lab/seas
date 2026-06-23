"""Persistent-context contract tests (argo_webhook.build_system_prompt).

F2(b): a conservative, FACTUAL context block is injected into Argo's system prompt
so it stays continuous across turns. Two things this locks in:

  (a) WHEN SOURCES HAVE DATA, the built prompt contains the block -- a stable marker
      plus at least one injected fact (a world_model belief, the active project). A
      prompt refactor that drops it fails here before it ships a regression.
  (b) WHEN SOURCES ARE MISSING/EMPTY/CORRUPT, the block is omitted (empty), the
      prompt is still valid, and nothing crashes -- the F1 placement lesson: never
      depend on a source that may be absent on the live runtime.

Pure: world_model + the project log are patched to tmp fixtures (no real data/*.json,
no network, no LLM). Sources confirmed present on the Railway runtime only --
private/decisions/*.md is gitignored and is deliberately NOT a source.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_webhook as wh
import world_model

_PROFILE = {"name": "Yiya", "one_liner": "a builder", "persona": "Plain.",
            "subject": "she", "object": "her", "possessive": "her"}

_BELIEFS = [
    {"id": "WM-001", "claim": "Agentic pathology beats baselines on VQA.",
     "confidence": 0.55, "status": "active",
     "evidence": [], "refutations": [], "predictions": []},
    {"id": "WM-002", "claim": "Frontier agents finish under 30% of long-horizon tasks.",
     "confidence": 0.30, "status": "unverified",
     "evidence": [], "refutations": [], "predictions": []},
]

_PROJECTS = [
    {"id": "P-001",
     "text": "⚓ Argo\n\nThis week's bet:\nPathology Eval Harness\n\nBuild a small eval.",
     "energy": 8, "shown_at": "2026-06-20T01:00:00.000Z"},
]


class _Fixtures:
    """Patch world_model + the project log to tmp files for the duration of a test."""

    def __init__(self, beliefs, projects):
        self._td = tempfile.TemporaryDirectory()
        d = Path(self._td.name)
        self.wm = d / "wm.json"
        self.pj = d / "pj.json"
        if beliefs is not None:
            self.wm.write_text(json.dumps(beliefs))
        if projects is not None:
            self.pj.write_text(json.dumps(projects))
        self._patches = [
            mock.patch.object(world_model, "WORLD_MODEL_PATH", self.wm),
            mock.patch.object(wh, "PROJECTS_LOG", self.pj),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        self._td.cleanup()
        return False


class PersistentContextPopulatedTest(unittest.TestCase):
    """(a) Sources have data -> the prompt carries the marker + injected facts."""

    def test_block_present_with_marker_and_facts(self):
        with _Fixtures(_BELIEFS, _PROJECTS):
            prompt = wh.build_system_prompt(_PROFILE)
        # Stable marker present.
        self.assertIn(wh.PERSISTENT_CONTEXT_MARKER, prompt)
        # At least one world_model fact (top belief, highest-confidence first).
        self.assertIn("WM-001", prompt)
        self.assertIn("Agentic pathology beats baselines on VQA.", prompt)
        # The active project fact, with its energy rating.
        self.assertIn("Pathology Eval Harness", prompt)
        self.assertIn("8/10", prompt)

    def test_facts_only_no_personality_tokens(self):
        # Voice stance: the block injects FACTS, not tone. It must not smuggle
        # peppy/personality filler into the prompt's context section.
        with _Fixtures(_BELIEFS, _PROJECTS):
            block = wh._persistent_context_block()
        low = block.lower()
        for banned in ("!", "excited", "awesome", "let's", "love", "amazing"):
            self.assertNotIn(banned, low)

    def test_world_model_only_still_emits_block(self):
        # The project log being empty must not suppress the world_model facts.
        with _Fixtures(_BELIEFS, []):
            block = wh._persistent_context_block()
        self.assertIn(wh.PERSISTENT_CONTEXT_MARKER, block)
        self.assertIn("WM-001", block)
        self.assertNotIn("Pathology Eval Harness", block)


class ActiveProjectLineAttributionTest(unittest.TestCase):
    """The energy rating in the active-project fact was written by record_rating
    when the HUMAN sent a 1-10. The fact lands in the SYSTEM prompt where "you" =
    Argo, so phrasing it "you rated it N/10" misattributes the rating's author to
    Argo. It must be neutrally, correctly attributed to the builder/human."""

    def test_energy_line_attributes_rating_to_builder_not_argo(self):
        with _Fixtures(_BELIEFS, _PROJECTS):
            line = wh._active_project_line()
        # The project fact carries the energy value...
        self.assertIn("8/10", line)
        # ...but never as "you rated" (that reads as Argo rating it in-prompt).
        self.assertNotIn("you rated", line.lower())
        # It is correctly attributed to the human who actually rated it.
        self.assertIn("builder", line.lower())


class ActiveProjectLineVisibilityTest(unittest.TestCase):
    """The active-project fact makes a "currently looking at" VISIBILITY claim.
    target_project falls back to the LAST log entry when no project has shown_at
    (that fallback is the bare-rating-attachment target, not a visibility signal),
    so a project that was never shown must NOT produce the fact -- else Argo carries
    a false "what they're viewing" claim across turns."""

    # A bare last entry with NO shown_at: logged, but never delivered to the user.
    _NEVER_SHOWN = [
        {"id": "P-009",
         "text": "⚓ Argo\n\nThis week's bet:\nUnseen Draft\n\nA draft never shown.",
         "energy": 7},
    ]

    def test_no_shown_at_emits_no_visibility_fact(self):
        # Negative control: pre-fix, target_project's last-entry fallback returns
        # this row and _active_project_line emits the false fact (test fails);
        # post-fix it is gated on shown_at and returns '' (test passes).
        with _Fixtures(_BELIEFS, self._NEVER_SHOWN):
            line = wh._active_project_line()
            block = wh._persistent_context_block()
        self.assertEqual(line, "")
        self.assertNotIn("Unseen Draft", block)
        self.assertNotIn("currently looking at", block)
        # The world_model fact still rides along; only the visibility claim is gone.
        self.assertIn("WM-001", block)

    def test_shown_at_emits_visibility_fact(self):
        # A genuinely-shown project (has shown_at) DOES support the claim.
        with _Fixtures(_BELIEFS, _PROJECTS):
            line = wh._active_project_line()
            block = wh._persistent_context_block()
        self.assertIn("Pathology Eval Harness", line)
        self.assertIn("currently looking at", block)


class PersistentContextDegradeTest(unittest.TestCase):
    """(b) Missing/empty/corrupt sources -> block omitted, prompt still valid."""

    def test_empty_sources_omit_block_prompt_still_valid(self):
        with _Fixtures([], []):
            block = wh._persistent_context_block()
            prompt = wh.build_system_prompt(_PROFILE)
        self.assertEqual(block, "")
        self.assertNotIn(wh.PERSISTENT_CONTEXT_MARKER, prompt)
        # Prompt is still the full, valid prompt (a stable downstream anchor).
        self.assertIn("ATTRIBUTION", prompt)
        self.assertIn("You are Argo.", prompt)

    def test_missing_files_do_not_crash(self):
        # Files never created -> both loaders return their default, no exception.
        with _Fixtures(None, None):
            block = wh._persistent_context_block()
            prompt = wh.build_system_prompt(_PROFILE)
        self.assertEqual(block, "")
        self.assertIn("ATTRIBUTION", prompt)

    def test_corrupt_files_do_not_crash(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        (d / "wm.json").write_text("{not json")
        (d / "pj.json").write_text("garbage]")
        with mock.patch.object(world_model, "WORLD_MODEL_PATH", d / "wm.json"), \
                mock.patch.object(wh, "PROJECTS_LOG", d / "pj.json"):
            block = wh._persistent_context_block()
            prompt = wh.build_system_prompt(_PROFILE)
        self.assertEqual(block, "")
        self.assertIn("ATTRIBUTION", prompt)

    def test_wrong_shape_json_does_not_crash(self):
        # Valid JSON but the WRONG shape (a dict where a list is expected -- a
        # hand-edit or a half-written store). The loaders return it as-is; the
        # block must omit the fact, not raise (AttributeError inside
        # format_beliefs_for_prompt would crash a chat turn).
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        (d / "wm.json").write_text(json.dumps({"oops": "a dict"}))
        (d / "pj.json").write_text(json.dumps({"also": "a dict"}))
        with mock.patch.object(world_model, "WORLD_MODEL_PATH", d / "wm.json"), \
                mock.patch.object(wh, "PROJECTS_LOG", d / "pj.json"):
            block = wh._persistent_context_block()
            prompt = wh.build_system_prompt(_PROFILE)
        self.assertEqual(block, "")
        self.assertIn("ATTRIBUTION", prompt)


if __name__ == "__main__":
    unittest.main()
