########################
#  Imports and Libraries
########################

import argparse
import os
import requests
import sys

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


############
#  Constants
############

# Official ESPN API
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports"

# Official footbal-data.org API
FD_API = "https://api.football-data.org/v4"

# Register at https://www.football-data.org/client/register
FD_API_KEY = os.environ.get("FD_API_KEY", "")

# Limit the date range to 10 days, which is footbal-data maximal time window
FD_MAX_RANGE = 10

# football-data.org v4 status
FD_STATUS = {"SCHEDULED": "Scheduled", "TIMED": "Scheduled", "IN_PLAY": "Live", "PAUSED": "Half-time", "FINISHED": "FT", "POSTPONED": "Postponed", "SUSPENDED": "Suspended", "CANCELLED": "Cancelled"}

# football-data.org fields consulted by the team filter (see _hit and fd_football functions)
FD_NAME_KEYS = ("name", "shortName", "tla")

# ESPN status.type.state states
ESPN_STATE = {"pre": "Scheduled", "in": "Live", "post": "FT"}

# ESPN fields consulted by the team filter (see _hit and espn_soccer functions)
ESPN_TEAM_KEYS = ("name", "displayName", "shortDisplayName", "shortName", "abbreviation", "slug")

# ESPN fields consulted by the athlete filter (see _hit and espn_tennis functions)
ESPN_ATHLETE_KEYS = ("name", "displayName", "shortName")

# List of tuples containing information on which events to fetch. (source, code, label, team filter): `source` keys into `FETCH`, `code` is the league code for that API (or `None` for "everything in your FD plan"), `label` becomes the competition column, and `team filter` is an optional set that restricts events to named teams.
TASKS = [
    ("fd", "BSA", "Brasileirão Série A", {"FLA", "PAL"}),
    ("fd", "BL1", "Bundesliga", {"FCB"}),
    ("fd", "PD", "La Liga", {"BAR", "RMA"}),
    ("fd", "FL1", "Ligue 1", {"ASM", "PSG"}),
    ("fd", "PL", "Premier League", {"ARS", "CHE", "LIV", "MCI", "MUN"}),
    ("fd", "CL", "UEFA Champions League", {"ARS", "ASM", "BAR", "CHE", "MCI", "MUN", "PSG", "RMA"}),
    ("fd", "CLI", "Copa Libertadores", None),
    ("espn", "bra.copa_do_brazil", "Copa do Brasil", None),
    ("espn", "ger.dfb_pokal", "DFB Pokal", {"FCB"}),
    ("espn", "esp.copa_del_rey", "Copa del Rey", {"BAR", "RMA"}),
    ("espn", "fra.coupe_de_france", "Coupe de France", {"ASM", "PSG"}),
    ("espn", "eng.fa.cup", "FA Cup", {"ARS", "CHE", "LIV", "MCI", "MUN"}),
    ("espn", "eng.league_cup", "Carabao Cup", {"ARS", "CHE", "LIV", "MCI", "MUN"}),
    ("espn", "conmebol.sudamericana", "Copa Sudamericana", None),
    ("espn", "uefa.nations", "UEFA Nations League", {"England", "France", "Germany", "Spain"}),
    ("espn", "fifa.friendly", "Internationals", {"Brazil"}),
    ("f1", None, "Formula 1", None),
    ("tennis", "atp", "Tennis (ATP)", None),
    ("tennis", "atp", "Tennis (ATP)", {"Carlos Alcaraz", "Jannik Sinner", "Alexander Zverev"}),
]


##############
#  Event class
##############

# Define an 'Event' class that will be used to store event's information
@dataclass(slots=True)
class Event:
    source: str
    competition: str
    utc: datetime
    title: str
    status: str


###############
#  HTTP session
###############

# Initialize an HTTP session
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=2, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504))))


######################
#  Auxiliary functions
######################

def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    """ Sends an HTTP GET request and returns the response parsed as JSON. """
    r = _session.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def parse_utc(raw: str) -> datetime | None:
    """ Parses a timestamp string 'YYYY-MM-DDThh:mm:ss' and returns a datetime object timezone-aware and normalized to UTC. """
    if not isinstance(raw, str):
        return None
    else:
        s = raw.strip()
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

def week_range(days: int) -> tuple[date, date]:
    """ Given an integer n, outputs a pair of dates: (today, today + n-1 days). """
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("days must be a positive integer")
    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=days - 1)
    return start, end

def espn_dates(start: date, end: date) -> str:
    """ ESPN buckets by US date: pad one day each side, then post-filter on the UTC date. """
    return f"{start - timedelta(days=1):%Y%m%d}-{end + timedelta(days=1):%Y%m%d}"

def espn_status(ev: dict) -> str:
    """ Given an ESPN event's dictionary, returns 'Scheduled' or the event's current status provided by ESPN. """
    if not isinstance(ev, dict):
        raise TypeError(repr(ev)[:50] + " is not a dictionary")
    status = ev.get("status") or {}
    if not isinstance(status, dict):
        return "Unknown"
    st = status.get("type") or {}
    if not isinstance(st, dict):
        return "Unknown"
    state = st.get("state") or "pre"
    if state == "pre":
        return "Scheduled"
    return st.get("shortDetail") or st.get("detail") or ESPN_STATE.get(state, state)

def _norm(s: str) -> str:
    """ Transform a string into lowercase, collapse multiple whitespaces, and replace hyphens and underscores by spaces. """
    return " ".join(s.casefold().replace("-", " ").replace("_", " ").split())

def _terms(teams: set[str] | None) -> set[str]:
    """ Filter strings and apply function _norm to the items of a set. """
    return {_norm(t) for t in teams or () if isinstance(t, str) and t.strip()}

def _hit(who: dict, keys: tuple[str, ...], terms: set[str]) -> bool:
    """ Determine if the items in terms are values of the who dictionary corresponding to one of the items in keys. """
    if not isinstance(who, dict) or not terms:
        return False
    fields = {_norm(who[k]) for k in keys if isinstance(who.get(k), str) and who[k].strip()}
    norm_terms = {_norm(t) for t in terms}
    return bool(norm_terms & fields)


###########
#  Fetchers
###########

def fd_football(code: str | None, label: str | None, days: int, teams: set[str] | None = None) -> list[Event]:
    """ Query football-data.org and return a list of Event's. """
    if not FD_API_KEY:
        raise RuntimeError("FD_API_KEY not set")
    start, end = week_range(days)
    events: list[Event] = []
    terms = _terms(teams)
    cur = start
    while cur <= end:
        hi = min(cur + timedelta(days=FD_MAX_RANGE - 1), end)
        path = f"competitions/{code}/matches" if code else "matches"
        data = get_json(f"{FD_API}/{path}",
                        params={"dateFrom": cur.isoformat(), "dateTo": hi.isoformat()},
                        headers={"X-Auth-Token": FD_API_KEY, "User-Agent": "Mozilla/5.0 (compatible; SportsDataClient/1.0)"})
        if not isinstance(data, dict):
            raise ValueError(f"fd: expected a JSON object, got {type(data).__name__}")
        matches = data.get("matches")
        if not isinstance(matches, list):
            raise ValueError("fd: response has no 'matches' list")
        total = data.get("count")
        if not isinstance(total, int):
            rs = data.get("resultSet")
            total = rs.get("count") if isinstance(rs, dict) else None
        if isinstance(total, int) and total > len(matches):
            print(f"[warn] fd_football: FD reports {total} matches for {cur.isoformat()}..{hi.isoformat()} but returned only {len(matches)} — truncated response; showing partial data", file=sys.stderr)
        pg = data.get("pagination")
        if isinstance(pg, dict):
            page, pages = pg.get("currentPage", pg.get("page")), pg.get("totalPages")
            if isinstance(page, int) and isinstance(pages, int) and page < pages:
                print(f"[warn] fd_football: incomplete response (page {page} of {pages}) for {cur.isoformat()}..{hi.isoformat()}; showing partial data", file=sys.stderr)
        for m in matches:
            if not isinstance(m, dict) or not m.get("utcDate"):
                continue
            home = m.get("homeTeam") if isinstance(m.get("homeTeam"), dict) else {}
            away = m.get("awayTeam") if isinstance(m.get("awayTeam"), dict) else {}
            if teams and not (_hit(home, FD_NAME_KEYS, terms) or _hit(away, FD_NAME_KEYS, terms)):
                continue
            title = (f'{home.get("shortName") or home.get("name") or "TBC"}'
                     f' vs {away.get("shortName") or away.get("name") or "TBC"}')
            comp = m.get("competition") if isinstance(m.get("competition"), dict) else {}
            competition = label or comp.get("name") or "Football"
            dt = parse_utc(m["utcDate"])
            if dt is None:
                continue
            events.append(Event("fd", competition, dt, title, FD_STATUS.get(m.get("status"), m.get("status") or "?")))
        cur = hi + timedelta(days=1)
    return events

def espn_soccer(slug: str, label: str, days: int, teams: set[str] | None = None) -> list[Event]:
    """ Query ESPN's API and return a list of Event's. """
    start, end = week_range(days)
    terms = _terms(teams)

    def _scoreboard(dates: str) -> list:
        data = get_json(f"{ESPN_API}/soccer/{slug}/scoreboard", params={"dates": dates})
        if not isinstance(data, dict):
            raise ValueError(f"espn/{slug}: expected a JSON object, got {type(data).__name__}")
        raw = data.get("events")
        if raw is not None and not isinstance(raw, list):
            raise ValueError(f"espn/{slug}: 'events' is not a list")
        return raw or []

    raw: list = []
    cur = start - timedelta(days=1)
    while cur <= end + timedelta(days=1):
        try:
            raw += _scoreboard(f"{cur:%Y%m%d}")
        except (requests.RequestException, ValueError) as day_exc:
            print(f"[warn] espn/{slug}: {cur:%Y-%m-%d} skipped: {day_exc}", file=sys.stderr)
        cur += timedelta(days=1)

    events, seen, matched = [], 0, 0
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        dt = parse_utc(ev.get("date"))
        if dt is None or dt.date() < start or end < dt.date():
            continue
        seen += 1
        if teams:
            comps = ev.get("competitions")
            competition = next((c for c in comps if isinstance(c, dict)), {}) if isinstance(comps, list) else {}
            competitors = competition.get("competitors")
            competitors = competitors if isinstance(competitors, list) else []
            if not any(_hit(c.get("team") if isinstance(c, dict) else {}, ESPN_TEAM_KEYS, terms) for c in competitors):
                continue
            matched += 1
        events.append(Event("espn", label, dt, ev.get("name") or "?", espn_status(ev)))
    if teams and seen and not matched:
        print(f"[warn] espn/{slug}: team filter {sorted(teams)} matched none of {seen} events; use whole names or TLAs (e.g. 'MCI')", file=sys.stderr)
    return events

def espn_f1(code: str | None, label: str, days: int, teams: set[str] | None = None) -> list[Event]:
    """ Query ESPN's API and return a list of Event's. """
    start, end = week_range(days)
    data = get_json(f"{ESPN_API}/racing/{code or 'f1'}/scoreboard",
                    params={"dates": espn_dates(start, end)})
    if not isinstance(data, dict):
        raise ValueError(f"espn/f1: expected a JSON object, got {type(data).__name__}")
    raw = data.get("events")
    if raw is not None and not isinstance(raw, list):
        raise ValueError("espn/f1: 'events' is not a list")
    events = []
    for ev in raw or []:
        if not isinstance(ev, dict):
            continue
        gp = ev.get("name") or ev.get("shortName") or "?"
        comps = ev.get("competitions")
        sessions = [c for c in comps if isinstance(c, dict)] if isinstance(comps, list) else []
        if not sessions:
            sessions = [ev]
        for s in sessions:
            dt = parse_utc(s.get("date") or s.get("startDate") or ev.get("date"))
            if dt is None or dt.date() < start or end < dt.date():
                continue
            stype = s.get("type") if (s is not ev and isinstance(s.get("type"), dict)) else {}
            sname = stype.get("text") or stype.get("abbreviation")
            title = f"{gp} - {sname}" if sname else gp
            events.append(Event("f1", label, dt, title, espn_status(s if s.get("status") else ev)))
    return events

def espn_tennis(tour: str, label: str, days: int, teams: set[str] | None = None) -> list[Event]:
    """ Query ESPN's API and return a list of Event's. """
    def _tennis_matches(ev: dict):
        groupings = ev.get("groupings")
        if isinstance(groupings, list):
            for g in groupings:
                if isinstance(g, dict) and isinstance(g.get("competitions"), list):
                    yield from (c for c in g["competitions"] if isinstance(c, dict))
        comps = ev.get("competitions")
        if isinstance(comps, list):
            yield from (c for c in comps if isinstance(c, dict))

    def _athlete_hit(a: dict) -> bool:
        fields = {_norm(a[k]) for k in ESPN_ATHLETE_KEYS if isinstance(a.get(k), str) and a[k].strip()}
        tokens = {tok for f in fields for tok in f.split()}
        return bool(fields & terms) or any(t in tokens for t in terms)

    start, end = week_range(days)
    terms = _terms(teams)

    if teams:
        events, seen, matched, ids = [], 0, 0, set()
        cur = start - timedelta(days=1)
        while cur <= end + timedelta(days=1):
            try:
                data = get_json(f"{ESPN_API}/tennis/{tour}/scoreboard", params={"dates": f"{cur:%Y%m%d}"})
            except (requests.RequestException, ValueError) as exc:
                print(f"[warn] tennis/{tour}: {cur:%Y-%m-%d}: {exc}", file=sys.stderr)
                cur += timedelta(days=1)
                continue
            raw = data.get("events") if isinstance(data, dict) else None
            for ev in raw if isinstance(raw, list) else []:
                if not isinstance(ev, dict):
                    continue
                for m in _tennis_matches(ev):
                    if not isinstance(m, dict):
                        continue
                    dt = parse_utc(m.get("date") or m.get("startDate"))
                    if dt is None or dt.date() < start or end < dt.date():
                        continue
                    mid = m.get("id")
                    if mid is not None and mid in ids:
                        continue
                    if mid is not None:
                        ids.add(mid)
                    seen += 1
                    competitors = m.get("competitors")
                    competitors = competitors if isinstance(competitors, list) else []
                    athletes = [((c.get("athlete") or c.get("team") or {}) if isinstance(c, dict) else {}) for c in competitors]
                    if not any(_athlete_hit(a) for a in athletes):
                        continue
                    matched += 1
                    players = [a.get("displayName") or a.get("name") for a in athletes if isinstance(a, dict)]
                    title = " vs ".join(p for p in players if p) or ev.get("name") or "?"
                    events.append(Event("tennis", label, dt, title, espn_status(m)))
            cur += timedelta(days=1)
        if teams and seen and not matched:
            print(f"[warn] tennis/{tour}: player filter {sorted(teams)} matched none of {seen} matches; use full names or surnames (e.g. 'Jannik Sinner', 'Sinner')", file=sys.stderr)
        return events

    tournaments = {}
    cur = start - timedelta(days=1)
    while cur <= end + timedelta(days=1):
        try:
            data = get_json(f"{ESPN_API}/tennis/{tour}/scoreboard", params={"dates": f"{cur:%Y%m%d}"})
        except (requests.RequestException, ValueError) as exc:
            print(f"[warn] tennis/{tour}: {cur:%Y-%m-%d}: {exc}", file=sys.stderr)
            cur += timedelta(days=1)
            continue
        raw = data.get("events") if isinstance(data, dict) else None
        for ev in raw if isinstance(raw, list) else []:
            if not isinstance(ev, dict):
                continue
            key = ev.get("id") or ev.get("shortName") or ev.get("name") or "?"
            agg = tournaments.setdefault(key, {"title": ev.get("shortName") or ev.get("name") or "?", "first": None, "last": None, "states": set()})
            for m in _tennis_matches(ev):
                if not isinstance(m, dict):
                    continue
                dt = parse_utc(m.get("date") or m.get("startDate"))
                if dt is None or dt.date() < start or end < dt.date():
                    continue
                agg["states"].add(((m.get("status") or {}).get("type") or {}).get("state") or "pre")
                if agg["first"] is None or dt < agg["first"]:
                    agg["first"] = dt
                if agg["last"] is None or dt > agg["last"]:
                    agg["last"] = dt
        cur += timedelta(days=1)

    events = []
    for agg in tournaments.values():
        if agg["first"] is None:
            continue
        first, last = agg["first"], agg["last"]
        span = f"{first:%d %b}" if first.date() == last.date() else f"{first:%d %b}-{last:%d %b}"
        states = agg["states"]
        if "in" in states:
            status = "Live"
        elif states and states <= {"post"}:
            status = "FT"
        else:
            status = "Scheduled"
        events.append(Event("tennis", label, first, f"{agg['title']} ({span})", status))
    return events

FETCH = {"fd": fd_football, "espn": espn_soccer, "f1": espn_f1, "tennis": espn_tennis}


################
#  Main / Kernel
################

def dedupe(events: list[Event]) -> list[Event]:
    """ Given a list of events, remove duplicates and return another list of events without duplicates. """
    seen, out = set(), []
    for e in events:
        norm = lambda s: " ".join(s.split()).casefold()
        key = (norm(e.competition), e.utc, norm(e.title))
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out

def show(events: list[Event]) -> None:
    " Given a list of events, print a table with these events' information "

    # If there are no events to be printed, print "No events to show" and return None.
    if not events:
        print("No events to show.")
        return

    # Sort events by their time
    events = sorted(events, key=lambda e: e.utc)

    # Auxiliary function to calculate the width of each column in the table
    def col_width(label: str, cells: list[str], cap: int) -> int:
        return max(min(max(len(c) for c in cells) + 1, cap), len(label) + 1)

    wc = col_width("Competition", [e.competition for e in events], 26)
    wt = col_width("Event", [e.title for e in events], 46)

    # Auxiliary function that crops long cells
    def clip(text: str, width: int) -> str:
        return text if len(text) <= width else text[: width - 1] + "…"

    # Auxiliary function that formats the columns of the table
    def fmt(competition, day, clock, event, wc, wt):
        return (f"{clip(competition, wc):<{wc}}  {day:<12}{clock:<6}  "
                f"{clip(event, wt):<{wt}}")

    # Print the header of the table
    header = fmt("Competition", "Date", "UTC", "Event", wc, wt)
    print(header)
    print("-" * len(header))

    # Print the other rows (events) of the table
    prev = None
    for e in events:
        # Add a blank line between days
        if prev is not None and e.utc.date() != prev:
            print()
        prev = e.utc.date()
        print(fmt(e.competition, f"{e.utc:%a %d %b}", f"{e.utc:%H:%M}", e.title, wc, wt))

def main(argv=None) -> int:
    " Main orchestrating function: parses arguments, fetches events' information, and prints the resulting table "

    # Parse the arguments of the CLI
    ap = argparse.ArgumentParser(description="Fixtures in the coming week (football/F1/tennis).")
    ap.add_argument("--days", type=int, default=7, help="days ahead (default 7, capped at 10)")
    args = ap.parse_args(argv)

    # Check if args.days is between 1 and FD_MAX_RANGE
    if args.days < 1:
        print("[warn] --days must be >= 1; falling back to 7", file=sys.stderr)
        args.days = 7
    elif args.days > FD_MAX_RANGE:
        print(f"[warn] --days capped to {FD_MAX_RANGE} (football-data.org's max window)", file=sys.stderr)
        args.days = FD_MAX_RANGE

    # Check if the sources listed in TASKS are contained in FETCH
    unknown = sorted({s for s, *_ in TASKS if s not in FETCH})
    if unknown:
        ap.error(f"unknown source(s) in TASKS: {', '.join(unknown)} (known: {', '.join(sorted(FETCH))})")

    # Fetch events' information
    rows: list[Event] = []
    failed = 0
    for source, code, label, teams in TASKS:
        try:
            rows += FETCH[source](code, label, args.days, teams)
        except Exception as exc:
            failed += 1
            print(f"[warn] {label or code or source}: {exc}", file=sys.stderr)

    # Remove duplicates
    rows = dedupe(rows)

    # If there are no rows to be printed
    if not rows:
        if failed:
            print(f"No events: {failed} of {len(TASKS)} source(s) failed (see warnings above).", file=sys.stderr)
            return 1
        else:
            print(f"No events scheduled in the next {args.days} day(s).")
            return 0
    # Otherwise, print the resulting table
    else:
        show(rows)
        if failed:
            print(f"[warn] {failed} of {len(TASKS)} source(s) failed; the table above is partial.", file=sys.stderr)
            return 1
        else:
            return 0


if __name__ == "__main__":
    sys.exit(main())
