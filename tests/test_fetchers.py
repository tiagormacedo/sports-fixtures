from datetime import datetime, timezone

import pytest
import requests

import sports_fixtures as mod

# ---------- stubs for get_json ----------

def fd_match(home="PSG", away="RMA", utc="2025-01-11T20:00:00Z", status="TIMED",
             home_name="Paris Saint-Germain", away_name="Real Madrid"):
    return {"utcDate": utc, "status": status,
            "homeTeam": {"shortName": home, "name": home_name, "tla": home},
            "awayTeam": {"shortName": away, "name": away_name, "tla": away},
            "competition": {"name": "UEFA Champions League"}}

def espn_event(name="France vs Italy", date="2025-01-11T20:00:00Z", state="pre",
               teams=(("FRA", "France"), ("ITA", "Italy"))):
    return {"name": name, "date": date,
            "status": {"type": {"state": state, "shortDetail": "FT" if state == "post" else ""}},
            "competitions": [{"competitors": [{"team": {"abbreviation": tla, "displayName": full,
                                                        "slug": full.lower().replace(" ", "-")}}
                                              for tla, full in teams]}]}

def f1_event(name="British Grand Prix", date="2025-01-11T14:00:00Z", sessions=None):
    ev = {"name": name, "date": date, "status": {"type": {"state": "pre", "shortDetail": ""}}}
    if sessions is not None:
        ev["competitions"] = [{"date": d, "type": {"text": t}} for d, t in sessions]
    return ev

def tennis_match(mid="m1", date="2025-01-11T13:00:00Z", players=("Carlos Alcaraz", "Jannik Sinner"), state="pre"):
    return {"id": mid, "date": date, "status": {"type": {"state": state}},
            "competitors": [{"athlete": {"displayName": p, "name": p, "shortName": p}} for p in players]}

def tennis_tournament(matches, name="ATP Finals", tid="t1"):
    return {"id": tid, "shortName": name, "groupings": [{"competitions": matches}]}

# ---------- get_json (transport layer) ----------

class FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")
    def json(self):
        return self._payload

def test_get_json_passes_params_headers_and_15s_timeout(monkeypatch):
    seen = {}
    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResp({"ok": 1})
    monkeypatch.setattr(mod._session, "get", fake_get)
    assert mod.get_json("https://x/y", params={"a": 1}, headers={"h": "v"}) == {"ok": 1}
    assert (seen["url"], seen["params"], seen["headers"], seen["timeout"]) == \
        ("https://x/y", {"a": 1}, {"h": "v"}, 15)

def test_get_json_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(mod._session, "get", lambda *a, **k: FakeResp({}, status=404))
    with pytest.raises(requests.HTTPError):
        mod.get_json("https://x/y")

def test_session_has_the_retry_policy():
    adapter = mod._session.get_adapter("https://site.api.espn.com")
    assert adapter.max_retries.total == 2
    assert adapter.max_retries.backoff_factor == 1.5
    assert {429, 500, 502, 503, 504} <= set(adapter.max_retries.status_forcelist)

# ---------- fd_football ----------

def test_fd_football_happy_path(monkeypatch, frozen):
    calls = []
    def fake(url, params=None, headers=None):
        calls.append((url, params, headers))
        return {"matches": [fd_match()], "count": 1}
    monkeypatch.setattr(mod, "get_json", fake)
    events = mod.fd_football("CL", "UEFA Champions League", 3)
    assert len(events) == 1
    e = events[0]
    assert (e.source, e.competition, e.title, e.status) == \
        ("fd", "UEFA Champions League", "PSG vs RMA", "Scheduled")
    assert e.utc == datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)
    url, params, headers = calls[0]
    assert url == f"{mod.FD_API}/competitions/CL/matches"
    assert params == {"dateFrom": "2025-01-10", "dateTo": "2025-01-12"}
    assert headers["X-Auth-Token"] == "test-key-dummy"

@pytest.mark.parametrize("raw, shown", [
    ("SCHEDULED", "Scheduled"), ("TIMED", "Scheduled"), ("IN_PLAY", "Live"), ("PAUSED", "Half-time"),
    ("FINISHED", "FT"), ("POSTPONED", "Postponed"), ("MYSTERY", "MYSTERY"), (None, "?"),
])
def test_fd_status_mapping(monkeypatch, frozen, raw, shown):
    monkeypatch.setattr(mod, "get_json", lambda *a, **k: {"matches": [fd_match(status=raw)]})
    assert mod.fd_football("CL", "CL", 1)[0].status == shown

def test_fd_team_filter(monkeypatch, frozen):
    other = fd_match(home="BAR", away="SEV", home_name="Barcelona", away_name="Sevilla FC")
    monkeypatch.setattr(mod, "get_json", lambda *a, **k: {"matches": [other, fd_match()]})
    assert [e.title for e in mod.fd_football("CL", "CL", 1, teams={"PSG", "RMA"})] == ["PSG vs RMA"]
    assert len(mod.fd_football("CL", "CL", 1)) == 2          # no filter -> both

def test_fd_splits_windows_longer_than_10_days(monkeypatch, frozen):
    calls = []
    monkeypatch.setattr(mod, "get_json", lambda url, params=None, **k: calls.append(dict(params)) or {"matches": [], "count": 0})
    mod.fd_football("CL", "CL", 12)
    assert calls == [{"dateFrom": "2025-01-10", "dateTo": "2025-01-19"},
                     {"dateFrom": "2025-01-20", "dateTo": "2025-01-21"}]
    calls.clear()
    mod.fd_football("CL", "CL", 10)
    assert calls == [{"dateFrom": "2025-01-10", "dateTo": "2025-01-19"}]   # exactly 10 -> single request

def test_fd_no_code_uses_plain_matches_path(monkeypatch, frozen):
    seen = []
    monkeypatch.setattr(mod, "get_json", lambda url, **k: seen.append(url) or {"matches": []})
    mod.fd_football(None, None, 1)
    assert seen[0].endswith("/matches")

def test_fd_requires_api_key(monkeypatch):
    monkeypatch.setattr(mod, "FD_API_KEY", "")
    with pytest.raises(RuntimeError, match="FD_API_KEY"):
        mod.fd_football("CL", "CL", 1)

@pytest.mark.parametrize("payload", [[], "nope", {"matches": "x"}])
def test_fd_rejects_malformed_responses(monkeypatch, frozen, payload):
    monkeypatch.setattr(mod, "get_json", lambda *a, **k: payload)
    with pytest.raises(ValueError):
        mod.fd_football("CL", "CL", 1)

def test_fd_warns_on_truncated_response_and_pagination(monkeypatch, frozen, capsys):
    monkeypatch.setattr(mod, "get_json", lambda *a, **k:
                        {"matches": [fd_match()], "count": 5, "pagination": {"currentPage": 1, "totalPages": 3}})
    assert len(mod.fd_football("CL", "CL", 1)) == 1
    err = capsys.readouterr().err
    assert "truncated" in err and "page 1 of 3" in err

def test_fd_skips_malformed_matches_and_falls_back_to_tbc(monkeypatch, frozen):
    payload = {"matches": [None, {"status": "TIMED"}, fd_match(utc="garbage"), fd_match()]}
    monkeypatch.setattr(mod, "get_json", lambda *a, **k: payload)
    assert len(mod.fd_football("CL", "CL", 1)) == 1
    bare = {"matches": [{"utcDate": "2025-01-11T20:00:00Z", "status": None, "homeTeam": {}, "awayTeam": {}}]}
    monkeypatch.setattr(mod, "get_json", lambda *a, **k: bare)
    e = mod.fd_football("CL", None, 1)[0]
    assert (e.title, e.status, e.competition) == ("TBC vs TBC", "?", "Football")

# ---------- espn_soccer ----------

def test_espn_soccer_requests_one_bucket_per_padded_day(monkeypatch, frozen):
    dates = []
    monkeypatch.setattr(mod, "get_json", lambda url, params=None, **k:
                        dates.append(params["dates"]) or {"events": []})
    mod.espn_soccer("uefa.nations", "UNL", 3)
    assert dates == ["20250109", "20250110", "20250111", "20250112", "20250113"]

def test_espn_soccer_maps_and_postfilters_to_window(monkeypatch, frozen):
    evs = [espn_event("Before", date="2025-01-09T21:00:00Z"),
           espn_event("Inside", date="2025-01-11T21:00:00Z", state="post"),
           espn_event("After", date="2025-01-14T21:00:00Z"),
           espn_event("Bad", date="not a date")]
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": evs})
    events = mod.espn_soccer("uefa.nations", "UNL", 3)
    assert [(e.source, e.competition, e.title, e.status) for e in events] == \
        [("espn", "UNL", "Inside", "FT")]

def test_espn_soccer_team_filter_by_name_tla_and_slug(monkeypatch, frozen):
    evs = [espn_event("France vs Italy"), espn_event("Portugal vs Croatia", teams=(("POR", "Portugal"), ("CRO", "Croatia")))]
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": evs})
    for wanted in ({"Portugal"}, {"CRO"}, {"croatia"}):
        got = mod.espn_soccer("uefa.nations", "UNL", 3, teams=wanted)
        assert [e.title for e in got] == ["Portugal vs Croatia"]

def test_espn_soccer_warns_when_filter_matches_nothing(monkeypatch, frozen, capsys):
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [espn_event()]})
    assert mod.espn_soccer("uefa.nations", "UNL", 3, teams={"Atlético de Madrid"}) == []
    assert "matched none" in capsys.readouterr().err

def test_espn_soccer_tolerates_day_failures_and_bad_payloads(monkeypatch, frozen, capsys):
    def fake(url, params=None, **k):
        if params["dates"] == "20250110":
            raise requests.ConnectionError("boom")
        if params["dates"] == "20250112":
            return ["not", "a", "dict"]           # ValueError inside -> warned, not raised
        return {"events": [espn_event()]}
    monkeypatch.setattr(mod, "get_json", fake)
    events = mod.espn_soccer("uefa.nations", "UNL", 3)
    assert len(events) == 3
    err = capsys.readouterr().err
    assert "skipped" in err and "expected a JSON object" in err

# ---------- espn_f1 ----------

def test_espn_f1_emits_one_event_per_session_and_drops_out_of_window(monkeypatch, frozen):
    ev = f1_event(sessions=[("2025-01-10T12:00:00Z", "Practice 1"),
                            ("2025-01-11T14:00:00Z", "Qualifying"),
                            ("2025-01-13T15:00:00Z", "Race")])
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [ev]})
    events = mod.espn_f1("f1", "Formula 1", 3)    # window 01-10..01-12
    assert [(e.title, e.utc.date().isoformat(), e.status) for e in events] == [
        ("British Grand Prix - Practice 1", "2025-01-10", "Scheduled"),
        ("British Grand Prix - Qualifying", "2025-01-11", "Scheduled")]

def test_espn_f1_without_sessions_uses_event_itself(monkeypatch, frozen):
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [f1_event()]})
    events = mod.espn_f1("f1", "Formula 1", 3)
    assert len(events) == 1 and events[0].title == "British Grand Prix"

def test_espn_f1_default_slug_and_payload_validation(monkeypatch, frozen):
    urls = []
    monkeypatch.setattr(mod, "get_json", lambda url, **k: urls.append(url) or {"events": []})
    mod.espn_f1(None, "Formula 1", 1)
    assert urls[0] == f"{mod.ESPN_API}/racing/f1/scoreboard"
    for bad in ([], {"events": 3}):
        monkeypatch.setattr(mod, "get_json", lambda url, b=bad, **k: b)
        with pytest.raises(ValueError):          # espn_f1 raises; it does not swallow per-day errors
            mod.espn_f1("f1", "Formula 1", 1)

# ---------- espn_tennis ----------

def test_espn_tennis_player_filter_matches_full_name_and_single_token(monkeypatch, frozen):
    data = {"events": [tennis_tournament([tennis_match("m1", players=("Novak Djokovic", "Jannik Sinner")),
                                          tennis_match("m2", players=("Casper Ruud", "Daniil Medvedev"))])]}
    monkeypatch.setattr(mod, "get_json", lambda url, **k: data)
    assert [e.title for e in mod.espn_tennis("atp", "ATP", 3, teams={"Sinner"})] == \
        ["Novak Djokovic vs Jannik Sinner"]
    assert mod.espn_tennis("atp", "ATP", 3, teams={"Carlos Alcaraz"}) == []

def test_espn_tennis_dedupes_by_match_id_across_padded_days(monkeypatch, frozen):
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [tennis_tournament([tennis_match("m1")])]})
    assert len(mod.espn_tennis("atp", "ATP", 3, teams={"Sinner"})) == 1   # 5 days, same id -> 1 event

def test_espn_tennis_warns_when_no_player_matches(monkeypatch, frozen, capsys):
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [tennis_tournament([tennis_match("m1")])]})
    assert mod.espn_tennis("atp", "ATP", 3, teams={"Roger Federer"}) == []
    assert "matched none" in capsys.readouterr().err

def test_espn_tennis_aggregates_tournaments_without_filter(monkeypatch, frozen):
    evs = [tennis_tournament([tennis_match("m1", date="2025-01-10T13:00:00Z"),
                              tennis_match("m2", date="2025-01-12T13:00:00Z")], name="ATP Finals"),
           tennis_tournament([tennis_match("m3", date="2025-01-11T13:00:00Z")], name="WTA Finals", tid="t2")]
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": evs})
    got = mod.espn_tennis("atp", "ATP", 3)
    assert {e.title for e in got} == {"ATP Finals (10 Jan-12 Jan)", "WTA Finals (11 Jan)"}  # %b assumes C locale
    assert all(e.source == "tennis" and e.status == "Scheduled" for e in got)

@pytest.mark.parametrize("states, expected", [
    ({"pre"}, "Scheduled"), ({"post"}, "FT"), ({"pre", "post"}, "Scheduled"),
    ({"in"}, "Live"), ({"pre", "in", "post"}, "Live"),
])
def test_espn_tennis_status_aggregation(monkeypatch, frozen, states, expected):
    matches = [tennis_match(f"m{i}", state=s) for i, s in enumerate(sorted(states), start=1)]
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": [tennis_tournament(matches)]})
    assert mod.espn_tennis("atp", "ATP", 3)[0].status == expected

def test_espn_tennis_drops_empty_or_out_of_window_tournaments(monkeypatch, frozen):
    evs = [tennis_tournament([], name="Empty"),
           tennis_tournament([tennis_match("m1", date="2025-01-20T13:00:00Z")], name="Too late")]
    monkeypatch.setattr(mod, "get_json", lambda url, **k: {"events": evs})
    assert mod.espn_tennis("atp", "ATP", 3) == []
