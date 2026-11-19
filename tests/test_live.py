import os
import pytest

import sports_fixtures as mod

@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("FD_API_KEY", "test-key-dummy") == "test-key-dummy",
    reason="needs a real FD_API_KEY",
)
def test_live_football_data_smoke():
    mod.FD_API_KEY = os.environ["FD_API_KEY"]  # module reads the key at import time
    events = mod.fd_football("CL", "UEFA Champions League", 3, teams={"PSG", "RMA"})
    assert all(e.source == "fd" and " vs " in e.title for e in events)

@pytest.mark.network
def test_live_espn_smoke():
    assert isinstance(mod.espn_f1("f1", "Formula 1", 2), list)
