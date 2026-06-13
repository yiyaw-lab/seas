"""Pytest configuration for the seas test suite.

The application modules live in ``src/`` and import each other by bare name
(``import argo_memory``), which works in production because every entrypoint is
run as ``python src/<script>.py`` — Python puts the script's own directory
(``src/``) at the front of ``sys.path``, so siblings resolve. Pytest collects
from the repo root instead, where ``src/`` is not on the path, so without this
file every test module fails to import with ``ModuleNotFoundError: argo_memory``.

Add ``src/`` to ``sys.path`` so collection mirrors the production import model.
"""

import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
