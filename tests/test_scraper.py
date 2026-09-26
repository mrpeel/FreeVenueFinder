"""Unit tests for PlayHQ scraper and parser."""

import datetime
from pathlib import Path
import pytest

from playhq_venue_tracker.models import MatchRecord, parse_time_slot
from playhq_venue_tracker.scraper import (
    PlayHQScraper,
    load_fixtures_from_cache,
    save_fixtures_to_cache,
)


def test_parse_time_slot_24hr():
    assert parse_time_slot("08:30") == "Morning"
    assert parse_time_slot("11:59") == "Morning"
    assert parse_time_slot("12:00") == "Afternoon"
    assert parse_time_slot("13:30") == "Afternoon"
    assert parse_time_slot("17:00") == "Afternoon"


def test_parse_time_slot_24hr_with_seconds():
    assert parse_time_slot("08:30:00") == "Morning"
    assert parse_time_slot("11:59:59") == "Morning"
    assert parse_time_slot("12:00:00") == "Afternoon"
    assert parse_time_slot("12:30:00") == "Afternoon"
    assert parse_time_slot("13:00:00") == "Afternoon"


def test_parse_time_slot_12hr_am_pm():
    assert parse_time_slot("8:30 AM") == "Morning"
    assert parse_time_slot("11:45 am") == "Morning"
    assert parse_time_slot("12:00 PM") == "Afternoon"
    assert parse_time_slot("12:30:00 PM") == "Afternoon"
    assert parse_time_slot("1:00 PM") == "Afternoon"
    assert parse_time_slot("2:30pm") == "Afternoon"


def test_parse_time_slot_iso():
    assert parse_time_slot("2026-10-03T09:00:00Z") == "Morning"
    assert parse_time_slot("2026-10-03T13:00:00+10:00") == "Afternoon"


def test_parse_time_slot_fallback_grade():
    assert parse_time_slot(None, grade_name="Under 14 A Junior") == "Morning"
    assert parse_time_slot("", grade_name="Senior 1st XI Turf") == "Afternoon"
    assert parse_time_slot(None, grade_name="Random Unspecified League") == "Unknown"


def test_extract_next_data_json_from_file():
    fixture_path = Path(__file__).parent / "fixtures" / "mock_playhq_page.html"
    html = fixture_path.read_text(encoding="utf-8")

    scraper = PlayHQScraper()
    data = scraper.extract_next_data_json(html)
    assert data is not None
    assert "props" in data
    assert "pageProps" in data["props"]


def test_extract_matches_from_mock_html(mock_html: str):
    scraper = PlayHQScraper()
    matches = scraper.extract_matches_from_html(mock_html, source_url="test:mock")
    assert len(matches) > 0
    first = matches[0]
    assert isinstance(first, MatchRecord)
    assert first.match_id.startswith("m-")
    assert first.date >= datetime.date(2026, 10, 3)
    assert first.venue_name != ""
    assert first.home_team != ""
    assert first.away_team != ""
    assert first.time_slot in ("Morning", "Afternoon")


def test_defensive_extraction_missing_fields():
    """Ensure scraper handles missing, null, or malformed fields without KeyError."""
    scraper = PlayHQScraper()

    # Incomplete payloads
    malformed_payload = {
        "props": {
            "pageProps": {
                "games": [
                    # Missing time, missing surface
                    {
                        "id": "g-sparse-1",
                        "date": "2026-10-03",
                        "home": "Home Club",
                        "away": "Away Club",
                        "venue": "Some Park",
                    },
                    # Missing id (should be omitted safely)
                    {
                        "date": "2026-10-03",
                        "home": "Home Club",
                        "away": "Away Club",
                        "venue": "Some Park",
                    },
                    # Missing venue (should be omitted safely)
                    {
                        "id": "g-no-venue",
                        "date": "2026-10-03",
                        "home": "Home Club",
                        "away": "Away Club",
                    },
                    # Teams as list of dicts, allocation nested differently
                    {
                        "gameId": "g-alt-format",
                        "gameDate": "2026-10-10T13:30:00Z",
                        "teams": [{"name": "Alpha CC"}, {"name": "Beta CC"}],
                        "allocation": {
                            "court": {
                                "name": "Oval 2",
                                "venue": {"name": "Kalang Park"},
                            }
                        },
                        "gradeName": "Senior Turf",
                    },
                ]
            }
        }
    }

    matches = scraper.extract_matches_from_json(malformed_payload)
    assert len(matches) == 2
    ids = [m.match_id for m in matches]
    assert "g-sparse-1" in ids
    assert "g-alt-format" in ids

    alt_match = next(m for m in matches if m.match_id == "g-alt-format")
    assert alt_match.home_team == "Alpha CC"
    assert alt_match.away_team == "Beta CC"
    assert alt_match.venue_name == "Kalang Park"
    assert alt_match.surface_name == "Oval 2"
    assert alt_match.time_slot == "Afternoon"


def test_cache_save_and_load(tmp_path: Path, mock_matches: list[MatchRecord]):
    cache_file = tmp_path / "fixtures_cache.json"

    saved = save_fixtures_to_cache(mock_matches, cache_file)
    assert saved.exists()

    loaded = load_fixtures_from_cache(cache_file)
    assert len(loaded) == len(mock_matches)
    assert loaded[0].match_id == mock_matches[0].match_id
    assert loaded[0].date == mock_matches[0].date


def test_graphql_round_and_grade_hint_parsing():
    """Verify that GraphQL discoverGradeFixture response extracts rounds and default grade properly."""
    scraper = PlayHQScraper()
    graphql_data = {
        "data": {
            "discoverGradeFixture": [
                {
                    "id": "round-1",
                    "name": "Round 1",
                    "games": [
                        {
                            "id": "game-101",
                            "date": "2026-10-03",
                            "allocation": {
                                "time": "12:30:00",
                                "court": {
                                    "name": "Kalang Park",
                                    "venue": {"name": "Kalang Park"},
                                },
                            },
                            "home": {"name": "Laburnum - 2nd XI"},
                            "away": {"name": "East Burwood - 2nd XI"},
                        }
                    ],
                }
            ]
        }
    }

    matches = scraper.extract_matches_from_json(
        graphql_data,
        source_url="graphql:test",
        default_grade_name="A Grade (80 Overs, 12 Players)",
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.match_id == "game-101"
    assert m.date == datetime.date(2026, 10, 3)
    assert m.start_time == "12:30:00"
    assert m.time_slot == "Afternoon"
    assert m.grade_name == "A Grade (80 Overs, 12 Players)"
    assert m.round_name == "Round 1"
    assert m.venue_name == "Kalang Park"
    assert m.home_team == "Laburnum - 2nd XI"
    assert m.away_team == "East Burwood - 2nd XI"

