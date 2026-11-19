from datetime import datetime, timezone

import pytest

import sports_fixtures as mod

def stub(events=(), error=None):
    """A fake fetcher that records its arguments."""
    calls = []
    def fetch(code, label, days, teams=None):
        calls.append((code, label, days, teams))
        if error is not None:
            raise error
        return list(events)
    fetch.calls = calls
    return fetch

def _ev(title="PSG vs RMA", comp="Champions League"):
    return mod.Event("fd", comp, datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc), title, "Scheduled")

def patch_all(monkeypatch, fd, espn=None, f1=None, tennis=None):
    """Replace ALL four sources so no test ever touches the network."""
    monkeypatch.setitem(mod.FETCH, "fd", fd)
    monkeypatch.setitem(mod.FETCH, "espn", espn or stub())
    monkeypatch.setitem(mod.FETCH, "f1", f1 or stub())
    monkeypatch.setitem(mod.FETCH, "tennis", tennis or stub())

def test_main_prints_table_and_returns_zero(monkeypatch, capsys):
    patch_all(monkeypatch, stub([_ev()]),
              tennis=stub([mod.Event("tennis", "Tennis (ATP)",
                                     datetime(2025, 1, 12, 18, 0, tzinfo=timezone.utc),
                                     "Alcaraz vs Sinner", "Scheduled")]))
    assert mod.main(["--days", "3"]) == 0
    out = capsys.readouterr().out
    assert "Competition" in out and "PSG vs RMA" in out and "Alcaraz vs Sinner" in out

def test_main_days_fallback_cap_and_default(monkeypatch, capsys):
    fd = stub()
    patch_all(monkeypatch, fd)
    assert mod.main([]) == 0                 # default
    assert fd.calls[0][2] == 7
    assert mod.main(["--days", "0"]) == 0    # below 1 -> falls back to 7
    assert fd.calls[-1][2] == 7
    assert mod.main(["--days", "99"]) == 0   # capped at FD_MAX_RANGE
    assert fd.calls[-1][2] == 10
    err = capsys.readouterr().err
    assert "--days must be >= 1" in err and "capped to 10" in err

def test_main_exit_codes(monkeypatch, capsys):
    patch_all(monkeypatch, stub(error=RuntimeError("down")))
    assert mod.main(["--days", "1"]) == 1    # all failed, nothing to show
    assert "No events: 4 of 4 source(s) failed" in capsys.readouterr().err

    e = _ev()
    patch_all(monkeypatch, stub([e]), espn=stub(error=RuntimeError("down")))
    cap = capsys.readouterr()
    assert mod.main(["--days", "1"]) == 1    # partial failure: table + exit 1
    cap = capsys.readouterr()
    assert "PSG vs RMA" in cap.out and "partial" in cap.err

    patch_all(monkeypatch, stub())           # no failures, no events
    assert mod.main(["--days", "1"]) == 0
    assert "No events scheduled" in capsys.readouterr().out

def test_main_dedupes_across_sources(monkeypatch, capsys):
    e = _ev()
    patch_all(monkeypatch, stub([e]),
              espn=stub([mod.Event("espn", e.competition, e.utc, e.title, "Live")]))
    mod.main(["--days", "1"])
    assert capsys.readouterr().out.count("PSG vs RMA") == 1

def test_main_rejects_unknown_task_source(monkeypatch):
    monkeypatch.setattr(mod, "TASKS", [("bogus", None, "X", None)])
    with pytest.raises(SystemExit) as ei:    # argparse ap.error -> exit code 2
        mod.main(["--days", "1"])
    assert ei.value.code == 2

def test_tasks_and_fetch_stay_in_sync():
    assert {s for s, *_ in mod.TASKS} == set(mod.FETCH)
    assert all(callable(f) for f in mod.FETCH.values())
