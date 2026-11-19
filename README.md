# Sports fixtures

![tests](https://github.com/tiagormacedo/sports-fixtures/actions/workflows/tests.yml/badge.svg)

A tiny, dependency-light CLI that prints upcoming sports fixtures as a sorted, deduplicated table in your terminal.

```text
Competition               Date         UTC    Event
-------------------------------------------------------
UEFA Champions League     Tue 19 Nov   20:00  Sporting CP vs Arsenal
Brasileirão Série A       Wed 20 Nov   22:30  Flamengo vs Palmeiras
Premier League            Sat 22 Nov   15:30  Arsenal vs Chelsea
Formula 1                 Sat 22 Nov   17:00  Las Vegas Grand Prix - Qualifying
Tennis (ATP)              Sun 23 Nov   14:00  Jannik Sinner vs Alexander Zverev
```

The data comes from [football-data.org](https://www.football-data.org) API and [ESPN's](https://site.api.espn.com/apis/site/v2/sports) public scoreboard API.


## Installation and Usage

```bash
# Clone the repository
git clone https://github.com/tiagormacedo/sports-fixtures.git
cd sports-fixtures

# Set up a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install the package and its dependencies
pip install -e .

# Set the API key
export FD_API_KEY="your-key-here"     # Linux or Mac
set FD_API_KEY=your-key-here          # Windows

# Collect the fixtures scheduled for the next 3 days
python -m sports_fixtures --days 3
```

If no `-- days` parameter is given, it defaults to a 7-day period. Due to football-data.org's limit, the `--days` parameter is capped at 10 days. All times are displayed in UTC. Warnings (`[warn] ...`) are written to stderr. If a source fails you still receive a table with whatever succeeded.


## Configuration

Edit the `TASKS` set in `src/sports_fixtures.py`. Each entry is a tuple `(source, code, label, team_filter)`, where:

| source | code | label | team filter |
|---|---|---|---|
| `"fd"`   | football-data.org competition code (`PL`, `BL1`, `PD`, `FL1`, `CL`, …) or `None` = all matches in your plan | free-text column value | set of TLAs/short names, e.g. `{"ARS", "MCI"}`, or `None` |
| `"espn"` | ESPN soccer slug, e.g. `eng.1`, `esp.1`, `uefa.champions`, `uefa.nations` | free-text | set of full names, e.g. `{"Arsenal", "Real Madrid"}`, or `None` |
| `"f1"`   | `None` (defaults to F1) | free-text | `None` (unused) |
| `"tennis"` | `"atp"` or `"wta"` | free-text | `None` → one row per tournament (with date span); set of player names → one row per match of those players |

Filters match after normalization (case, whitespace, `-`/`_` → space), so `"Real Madrid"` also matches `real-madrid`. Football-data.org teams are best matched by TLA.


## Features

- One command, multiple sports.
- Declarative configuration: just edit the `TASKS` set of tuples `(source, code, label, team filter)`.
- Teams and players filters.
- Automatic retries with backoff, per-day/per-task error isolation, and warnings instead of crashes.
- Deduplication, day-grouped output, width-capped columns, UTC times throughout.
- Meaningful exit codes: `0` success, `1` partial/total failure (details on `stderr`), `2` usage error.


## Requirements

- **Python 3.11+** (ESPN timestamps omit seconds; `datetime.fromisoformat` needs 3.11+ to parse them).
- [`requests`](https://pypi.org/project/requests/) and [`urllib3`](https://pypi.org/project/urllib3/).
- A **football-data.org API key**, which can be obtained for free at
  <https://www.football-data.org/client/register>. Without it, some football tasks fail (with warnings) but others still work.


## Development

The test suite lives in `tests/` and runs automatically in CI on every push and
pull request (Python 3.11–3.13). Unit tests are hermetic: HTTP is mocked at the
transport layer and the clock is frozen, so nothing touches the network.

```bash
pip install -e ".[dev]"
pytest --cov --cov-report=term-missing
FD_API_KEY="your-key-here" pytest -m network
```

Live-API tests are marked `network` and never run in CI, so a third-party
outage can't fail your build. Run them occasionally to catch response-schema
drift in the ESPN / football-data.org APIs.

| File | Covers |
|---|---|
| `tests/test_pure.py` | pure helpers (`parse_utc`, `week_range`, `dedupe`, `show`, …) |
| `tests/test_fetchers.py` | fetchers with mocked HTTP (`get_json`, `fd_football`, `espn_*`) |
| `tests/test_main.py` | CLI behavior: `--days` handling, exit codes, cross-source dedupe |
| `tests/test_live.py` | `network`-marked smoke tests against the real APIs |


## Project layout

```text
sports-fixtures/
├── .github
|   └── workflows
|     ├── sports_fixtures.yml
|     └── tests.yml
├── src/
|   └── sports_fixtures.py
├── tests/
│   ├── conftest.py
│   ├── test_fetchers.py
│   ├── test_live.py                  
│   ├── test_main.py
│   └── test_pure.py
├── .gitignore
├── LICENSE
├── pyproject.toml
├── README.md
└── this_weeks_fixtures.txt
```


## Limitations

- 10-day horizon: football-data.org's API caps query windows at 10 days; the CLI adopts the same cap globally.
- A full run issues ~50–70 HTTP GETs (one per day per ESPN slug); be gentle.
- UTC-only output.
- No caching.


## License

This project is licensed under the [MIT license](LICENSE). Data belongs to its providers (ESPN, football-data.org); respect their terms of use.
