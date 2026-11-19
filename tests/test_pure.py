from datetime import date, datetime, timedelta, timezone

import pytest

import sports_fixtures as mod

TODAY = date(2025, 1, 10)

# ---------- parse_utc ----------

@pytest.mark.parametrize("raw, expected", [
    ("2025-01-11T20:00:00Z",      datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)),
    ("2025-01-11T20:00:00z",      datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)),
    ("2025-01-11T22:00:00+02:00", datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)),  # offset converted
    ("2025-01-11T20:00:00",       datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)),  # naive -> UTC
    ("  2025-01-11T20:00:00Z  ",  datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)),
    ("2025-01-11T20:00:00.500Z",  datetime(2025, 1, 11, 20, 0, 0, 500000, tzinfo=timezone.utc)),
])
def test_parse_utc_accepts_valid_timestamps(raw, expected):
    assert mod.parse_utc(raw) == expected

@pytest.mark.parametrize("bad", ["", "garbage", "2025-13-01T00:00:00", None, 12345, [], date(2025, 1, 1)])
def test_parse_utc_returns_none_on_garbage(bad):
    assert mod.parse_utc(bad) is None

# ---------- week_range ----------

def test_week_range_windows(frozen):
    assert mod.week_range(1) == (TODAY, TODAY)
    assert mod.week_range(7) == (TODAY, TODAY + timedelta(days=6))
    assert mod.week_range(10) == (TODAY, TODAY + timedelta(days=9))

@pytest.mark.parametrize("bad", [0, -3, 1.5, "7", None, True, False])  # bools rejected explicitly
def test_week_range_rejects_non_positive_or_non_int(bad):
    with pytest.raises(ValueError):
        mod.week_range(bad)

# ---------- espn_dates ----------

def test_espn_dates_pads_one_day_each_side():
    assert mod.espn_dates(date(2025, 1, 1), date(2025, 1, 7)) == "20241231-20250108"

# ---------- espn_status ----------

def test_espn_status_defaults_to_scheduled():
    assert mod.espn_status({}) == "Scheduled"                       # no status at all
    assert mod.espn_status({"status": {}}) == "Scheduled"           # no type
    assert mod.espn_status({"status": {"type": {"state": "pre"}}}) == "Scheduled"

def test_espn_status_shortdetail_beats_detail_beats_state():
    ev = {"status": {"type": {"state": "in", "shortDetail": "34'", "detail": "1st Half"}}}
    assert mod.espn_status(ev) == "34'"
    assert mod.espn_status({"status": {"type": {"state": "in", "detail": "1st Half"}}}) == "1st Half"
    assert mod.espn_status({"status": {"type": {"state": "post"}}}) == "FT"          # ESPN_STATE fallback
    assert mod.espn_status({"status": {"type": {"state": "postponed"}}}) == "postponed"  # unknown echoed

def test_espn_status_unknown_and_type_error():
    assert mod.espn_status({"status": "weird"}) == "Unknown"
    assert mod.espn_status({"status": {"type": "weird"}}) == "Unknown"
    with pytest.raises(TypeError):
        mod.espn_status("not a dict")

# ---------- _norm / _terms / _hit ----------

@pytest.mark.parametrize("raw, expected", [
    ("  Manchester   City ", "manchester city"),
    ("Al-Ahli_SC", "al ahli sc"),
    ("PARIS-SAINT-GERMAIN", "paris saint germain"),
])
def test_norm(raw, expected):
    assert mod._norm(raw) == expected

def test_terms_filters_and_normalizes():
    assert mod._terms({"PSG", "  ", "", 42, None, "real-madrid"}) == {"psg", "real madrid"}
    assert mod._terms(None) == set()

def test_hit_matches_any_key_case_and_hyphen_insensitively():
    team = {"name": "Real Madrid CF", "shortName": "Real Madrid", "tla": "RMA"}
    assert mod._hit(team, mod.FD_NAME_KEYS, {"rma"})
    assert mod._hit(team, mod.FD_NAME_KEYS, {"REAL MADRID"})
    assert mod._hit({"displayName": "Al-Ahli"}, ("displayName",), {"al ahli"})

def test_hit_is_exact_field_matching_not_substring():
    team = {"name": "Real Madrid CF", "shortName": "Real Madrid", "tla": "RMA"}
    assert not mod._hit(team, mod.FD_NAME_KEYS, {"madrid"})      # substring does not match
    assert not mod._hit(team, mod.FD_NAME_KEYS, set())           # empty filter
    assert not mod._hit("RMA", mod.FD_NAME_KEYS, {"rma"})        # not a dict
    assert not mod._hit({"name": None, "tla": ""}, mod.FD_NAME_KEYS, {"rma"})

# ---------- dedupe ----------

def _ev(utc, title="PSG vs RMA", comp="Champions League", status="FT"):
    return mod.Event("fd", comp, utc, title, status)

def test_dedupe_ignores_case_whitespace_and_status():
    dt = datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)
    out = mod.dedupe([_ev(dt), _ev(dt, "  psg  VS rma ", status="Live"), _ev(dt)])
    assert len(out) == 1                       # status is NOT part of the key

def test_dedupe_keeps_distinct_events_in_first_seen_order():
    a = datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc)
    b = datetime(2025, 1, 12, 18, 0, tzinfo=timezone.utc)
    events = [_ev(a), _ev(b, "Elche vs Getafe"), _ev(a, comp="LaLiga")]
    out = mod.dedupe(events)
    assert [e.title for e in out] == ["PSG vs RMA", "Elche vs Getafe", "PSG vs RMA"]

# ---------- show ----------

def test_show_empty(capsys):
    mod.show([])
    assert capsys.readouterr().out.strip() == "No events to show."

def test_show_sorts_by_utc_and_groups_days(capsys):
    late = mod.Event("espn", "Nations League", datetime(2025, 1, 11, 21, 0, tzinfo=timezone.utc), "Portugal vs Croatia", "Scheduled")
    early = mod.Event("espn", "Nations League", datetime(2025, 1, 11, 18, 0, tzinfo=timezone.utc), "France vs Italy", "Scheduled")
    nxt = mod.Event("espn", "Nations League", datetime(2025, 1, 12, 18, 0, tzinfo=timezone.utc), "Spain vs Germany", "Scheduled")
    mod.show([late, nxt, early])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("Competition") and lines[1].startswith("-")
    assert out.index("France vs Italy") < out.index("Portugal vs Croatia")   # sorted within the day
    assert "\n\n" in out                                                     # blank line between days

def test_show_clips_long_cells(capsys):
    e = mod.Event("fd", "A" * 40, datetime(2025, 1, 11, 20, 0, tzinfo=timezone.utc), "T" * 60, "FT")
    mod.show([e])
    out = capsys.readouterr().out
    assert "A" * 25 + "…" in out and "A" * 40 not in out   # competition cap 26
    assert "T" * 45 + "…" in out and "T" * 60 not in out   # event cap 46
