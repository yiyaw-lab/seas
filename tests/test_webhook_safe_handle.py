"""_safe_handle is the outermost net for a chat turn (it runs in a daemon thread).

It must never let an exception OR a SystemExit escape: send_telegram.send_message
calls fail() -> sys.exit(1) on a delivery failure, and SystemExit is not an
Exception, so a bare `except Exception` let it kill the thread silently (the update
was already deduped, so the user got no reply and no retry). These lock that the
net catches both and logs the failure.
"""

import unittest
from unittest import mock

import argo_incidents
import argo_webhook as wh


class SafeHandleNetTest(unittest.TestCase):
    def test_systemexit_from_a_send_does_not_escape_and_is_logged(self):
        # The regression: send_message's sys.exit(1) raised SystemExit, which a bare
        # `except Exception` did NOT catch -> it escaped _safe_handle and killed the
        # daemon thread. (FAILS before the fix: the SystemExit propagates out here.)
        with mock.patch.object(argo_incidents, "record_incident", lambda *a, **k: None):
            with mock.patch.object(wh, "handle_update", side_effect=SystemExit(1)):
                with self.assertLogs("argo_webhook", level="ERROR") as cm:
                    wh._safe_handle({"update_id": 1})   # must return, not raise
        self.assertTrue(any("handle_update failed" in m for m in cm.output))

    def test_ordinary_exception_is_also_caught_and_logged(self):
        with mock.patch.object(argo_incidents, "record_incident", lambda *a, **k: None):
            with mock.patch.object(wh, "handle_update", side_effect=RuntimeError("boom")):
                with self.assertLogs("argo_webhook", level="ERROR") as cm:
                    wh._safe_handle({"update_id": 2})
        self.assertTrue(any("handle_update failed" in m for m in cm.output))


if __name__ == "__main__":
    unittest.main()
