"""Pytest configuration and shared fixtures."""

import datetime
from pathlib import Path
import pytest

from playhq_venue_tracker.config import VenueCatalog, load_config
from playhq_venue_tracker.mock_data import (
    build_mock_html_page,
    build_mock_next_data_payload,
    generate_mock_fixtures_dataset,
)
from playhq_venue_tracker.models import AppConfig, MatchRecord


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    config_content = """
association_url: "https://www.playhq.com/cricket-australia/org/eastern-cricket-association/summer-202627"
fixture_urls: []
date_range:
  start_date: "2026-10-03"
  end_date: "2027-02-27"
target_day: "Saturday"
time_slots:
  Morning:
    cutoff: "12:00"
  Afternoon:
    cutoff: "12:00"
venues:
  - name: "Kalang Park 1"
    aliases:
      - "Kalang Park Oval 1"
      - "Kalang Park #1"
  - name: "Kalang Park 2"
    aliases:
      - "Kalang Park Oval 2"
      - "Kalang Park #2"
  - name: "Surrey Park 1"
    aliases:
      - "Surrey Park Oval 1"
      - "Surrey Park"
  - name: "Box Hill City Oval"
    aliases:
      - "City Oval"
"""
    p = tmp_path / "test_config.yaml"
    p.write_text(config_content)
    return p


@pytest.fixture
def app_config(sample_config_path: Path) -> AppConfig:
    return load_config(sample_config_path)


@pytest.fixture
def mock_html() -> str:
    return build_mock_html_page()


@pytest.fixture
def mock_matches() -> list[MatchRecord]:
    return generate_mock_fixtures_dataset()
