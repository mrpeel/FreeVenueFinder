# PlayHQ Venue Tracker (`playhq-venue-tracker`)

A modular, robust Python CLI tool that scrapes public PlayHQ cricket fixtures across an entire association, maps scheduled matches to target grounds, and **inverts the schedule** to generate an availability matrix highlighting unoccupied/free grounds for every Saturday across the cricket season (default: `2026-10-03` to `2027-02-27`).

---

## Features

- **Next.js Hydration Extraction**: Directly extracts and recursively parses PlayHQ's `<script id="__NEXT_DATA__" type="application/json">` payload without requiring heavyweight, brittle headless browsers (like Selenium or Playwright).
- **Defensive Data Extraction**: Gracefully degrades when fields, nested objects, or competition structures change across junior and senior grades.
- **Robust HTTP Handling**: Desktop browser User-Agent headers, polite rate-limiting (`0.5–1.0s` sleep), session reuse, and automatic exponential backoff retries on HTTP 429/5xx status codes via `urllib3.util.retry.Retry`.
- **Venue Catalog & Alias Normalization**: Maps variations in PlayHQ ground naming (e.g., *"Kalang Park Oval 1"*, *"Kalang Park #1"*, *"Kalang Park (Oval 1)"*) to canonical venue entries.
- **Time Slot Separation**:
  - **Morning** (Juniors): Start times strictly before 12:00 PM.
  - **Afternoon** (Seniors): Start times 12:00 PM and later.
- **Schedule Inversion Engine**: Generates all target Saturdays and evaluates every `(Saturday, Venue, TimeSlot)` tuple to determine whether a ground is `BOOKED` (with grade and match details) or `AVAILABLE`.
- **Rich Visual Exports**:
  - **Excel Matrix (`.xlsx`)**: Styled multi-sheet workbook with green fills for `AVAILABLE` and soft red fills for `BOOKED` grounds, plus a complete raw match dump sheet.
  - **Flat CSV Summary (`.csv`)**: Columns: `Date`, `TimeSlot`, `Venue`, `Status`, `Grade`, `MatchDetails`.
  - **Rich Terminal Table**: Immediate console summary color-coded with ground availability counts.
- **Offline / Mock Mode**: Includes realistic synthetic fixtures for testing and offline verification (`--mock`).

---

## Architecture & Directory Layout

```text
FreeVenueFinder/
├── config.yaml                    # Master venue catalog, alias mappings, and date ranges
├── requirements.txt               # Locked project dependencies
├── pyproject.toml                 # Package setup and CLI entry point
├── playhq_venue_tracker/
│   ├── __init__.py                # Package metadata
│   ├── models.py                  # Pydantic V2 data models (MatchRecord, AvailabilityRecord, etc.)
│   ├── config.py                  # Configuration loader and VenueCatalog alias resolver
│   ├── scraper.py                 # PlayHQ HTTP client, __NEXT_DATA__ parser, and cache manager
│   ├── engine.py                  # Saturday generator, cross-referencing, and inversion logic
│   ├── exporters.py               # Formatted openpyxl Excel matrix, CSV, and Rich table
│   ├── mock_data.py               # Realistic mock data and Next.js HTML payloads
│   └── cli.py                     # Typer CLI application (fetch, analyze, run)
├── data/
│   └── fixtures_cache.json        # Raw cached match fixtures
├── output/
│   ├── available_grounds.csv   # Filtered available/free grounds sorted by Date & Distance from LCC
│   ├── venue_availability.xlsx # Multi-sheet formatted Excel workbook (Sheet 1: Available Grounds)
│   └── venue_availability.csv  # Complete availability log (available + booked)
└── tests/
    ├── conftest.py             # Pytest fixtures and mock setup
    ├── fixtures/
    │   └── mock_playhq_page.html # Sample Next.js HTML with __NEXT_DATA__
    ├── test_scraper.py         # Unit tests for scraper, defensive parser, time slot classifier
    ├── test_engine.py          # Unit tests for date generation, catalog, and inversion
    ├── test_exporters.py       # Unit tests for Excel styling, CSV, and terminal output
    └── test_cli.py             # Integration tests for CLI commands and end-to-end pipeline
```

---

## Installation

Ensure you have **Python 3.11+** installed.

```bash
# 1. Clone or navigate to the repository
cd FreeVenueFinder

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies and the CLI tool in editable mode
pip install -e .
```

Alternatively, install using `requirements.txt`:
```bash
pip install -r requirements.txt
```

## Finding Nearby Grounds & Populating `config.yaml`

A dedicated script and CLI command are provided to find all cricket grounds within any radius of Laburnum Cricket Club (`-37.82774221477076, 145.14233395005854`) or any custom GPS coordinate by querying the local cricket associations on PlayHQ (BHRDCA and ECA):

```bash
# 1. Discover grounds within 10km of Laburnum CC and display a formatted table
python find_nearby_venues.py --radius 10.0

# 2. Automatically populate config.yaml with all discovered grounds and their aliases
python find_nearby_venues.py --radius 10.0 --update-config --output-json data/nearby_grounds.json

# Alternatively, run via the CLI command:
playhq-venue-tracker find-venues --radius 10.0 --update-config
```

This maps:
- **82 unique venues** (and **117 individual ovals/surfaces**) within 10 km.
- Exact distances, suburbs, coordinates, and complete alias mappings (e.g., handling variations like *"Morton Park #1 - East"*, *"Morton Park Oval 1"*, *"Morton Park 1"*).

---

## Quick Start & Usage

The tool provides CLI commands: `find-venues`, `fetch`, `analyze`, and `run`.

### 1. Run End-to-End Pipeline (Fetch + Analyze)

Run the full pipeline using your `config.yaml`:
```bash
playhq-venue-tracker run --config config.yaml --export both
```

#### Test Offline with Realistic Mock Data:
```bash
playhq-venue-tracker run --mock --export both
```

Options for `run` and `analyze`:
- `--afternoon-only`: **Exclude morning games** and audit Afternoon slots only (Seniors).
- `--slot`, `-s`: Time slot to audit: `afternoon`, `morning`, or `all` (default: `all` or from config).
- `--date`: Filter output to a specific Saturday (e.g. `2026-10-03`).
- `--limit`: Maximum number of closest available grounds to display per date in console summary (default: `10`).
- `--config`, `-c`: Path to `config.yaml` (default: `config.yaml`).
- `--start-date`: Override start date (e.g. `2026-10-03`).
- `--end-date`: Override end date (e.g. `2027-02-27`).
- `--export`, `-e`: Export format: `both`, `xlsx`, `csv`, or `none` (default: `both`).
- `--output-dir`: Output directory (default: `output/`).
- `--cache-file`: Path for cached fixtures (default: `data/fixtures_cache.json`).
- `--mock`: Scrape realistic offline synthetic season data.
- `--verbose`, `-v`: Enable detailed debug logging.

#### Example: Afternoon Games Only, Closest Grounds
```bash
# Analyze cached fixtures for Afternoon slots:
playhq-venue-tracker analyze --afternoon-only --limit 10

# Filter for a specific round/date:
playhq-venue-tracker analyze --afternoon-only --date 2026-10-03
```

---

### 2. Step-by-Step Execution

#### Step A: `fetch` (Scrapes PlayHQ and saves raw fixtures)
```bash
playhq-venue-tracker fetch --config config.yaml --output data/fixtures_cache.json
```
- Fetches all configured or discovered grade fixture pages.
- Parses `<script id="__NEXT_DATA__">` JSON payloads and queries PlayHQ GraphQL endpoints.
- Writes validated fixtures to `data/fixtures_cache.json`.

#### Step B: `analyze` (Inverts schedule and generates matrices)
```bash
playhq-venue-tracker analyze --config config.yaml --cache data/fixtures_cache.json --export both
```
- Reads cached fixtures from `data/fixtures_cache.json`.
- Resolves raw grounds to canonical venues in the master catalog.
- Inverts schedule for all Saturdays between `2026-10-03` and `2027-02-27`.
- Displays a colored summary table in the terminal of the closest free grounds.
- Exports `output/available_grounds.csv`, `output/venue_availability.xlsx`, and `output/venue_availability.csv`.

---

## Output Formats

### 1. Dedicated Available Grounds CSV (`output/available_grounds.csv`)
High-priority flat list of only available (free) grounds, ordered strictly by `Date ASC, DistanceKm ASC`:
```csv
Date,Day,TimeSlot,DistanceKm,Venue,Suburb,HomeClub,HomeTeams,Competition
2026-10-03,Saturday,Afternoon,0.95,Whitehorse Reserve - Howard Wilson Oval,BOX HILL,East Box Hill,1st XI,BHRDCA
2026-10-03,Saturday,Afternoon,1.69,Mirrabooka Reserve 1,BLACKBURN SOUTH,Blackburn South,"1st XI, 2nd XI",BHRDCA
2026-10-03,Saturday,Afternoon,1.69,Mirrabooka Reserve 2,BLACKBURN SOUTH,,,
2026-10-03,Saturday,Afternoon,2.18,Springfield Park West,KERRIMUIR,Mont Albert,"4th XI, 3rd XI",ECA
2026-10-03,Saturday,Afternoon,2.46,Mahoneys Reserve 3,FOREST HILL,,,
2026-10-03,Saturday,Afternoon,2.46,Surrey Park 3,BOX HILL SOUTH,Mont Albert,"5th XI, U14",ECA
...
```

### 2. Excel Matrix Workbook (`output/venue_availability.xlsx`)
- **Sheet 1: Available Grounds**:
  - Filtered table of all free grounds, sorted by `Date ASC, Distance (km) ASC`.
  - Columns: `Date`, `Day`, `Time Slot`, `Distance from LCC (km)`, `Ground / Venue`, `Suburb`, `Home Club`, `Home Teams`, `Competition`.
- **Sheet 2: Afternoon Seniors**:
  - 2D grid: Rows = Every Saturday; Columns = Monitored grounds (sorted by distance from LCC).
  - Cells: Highlighted in green (`"AVAILABLE"`) or soft red (`"BOOKED: Grade - Home Team v Away Team"`).
- **Sheet 3: Morning Juniors**:
  - Identical matrix structure for Morning matches (if morning slots are audited).
- **Sheet 4: Raw Match Dump**:
  - Full tabular log of all scraped fixtures with match IDs, dates, start times, canonical venues, and teams.

### 3. Flat Availability Audit Log (`output/venue_availability.csv`)
Complete log of all venue-date-slot permutations (both `AVAILABLE` and `BOOKED`) with match details and home tenant info.

---

## Running the Tests

The test suite includes 24 unit and integration tests covering parser extraction, catalog alias normalization, inversion logic, tenant inference, Excel styling, distance sorting, and CLI workflows:

```bash
pytest -v
```
