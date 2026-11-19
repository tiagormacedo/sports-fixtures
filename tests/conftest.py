"""Shared fixtures. pytest imports conftest before any test module,
so the path bootstrap and env defaults below are guaranteed to run first."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

# Make `import sports_fixtures` work without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# sports_fixtures.py reads FD_API_KEY at import time — give it a dummy so
# imports in CI (where no real key exists) never break. Tests that need a
# specific value monkeypatch `sports_fixtures.FD_API_KEY` instead of the env
# var.
os.environ.setdefault("FD_API_KEY", "test-key-dummy")

FROZEN_NOW = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)  # a Friday, midday UTC

@pytest.fixture
def frozen():
    """Freeze `datetime.now(timezone.utc)` at 2025-01-10 12:00 UTC."""
    with freeze_time(FROZEN_NOW):
        yield FROZEN_NOW.date()  # date(2025, 1, 10)
