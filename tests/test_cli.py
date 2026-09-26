"""Integration tests for Typer CLI commands."""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from playhq_venue_tracker.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "playhq-venue-tracker" in result.output
    assert "fetch" in result.output
    assert "analyze" in result.output
    assert "run" in result.output


def test_cli_fetch_and_analyze_mock_pipeline(tmp_path: Path, sample_config_path: Path):
    cache_file = tmp_path / "fixtures_cache.json"
    output_dir = tmp_path / "out"

    # Step 1: fetch --mock
    fetch_result = runner.invoke(
        app,
        [
            "fetch",
            "--config", str(sample_config_path),
            "--output", str(cache_file),
            "--mock",
        ],
    )
    assert fetch_result.exit_code == 0
    assert cache_file.exists()

    # Step 2: analyze
    analyze_result = runner.invoke(
        app,
        [
            "analyze",
            "--config", str(sample_config_path),
            "--cache", str(cache_file),
            "--output-dir", str(output_dir),
            "--export", "both",
        ],
    )
    assert analyze_result.exit_code == 0
    assert (output_dir / "venue_availability.csv").exists()
    assert (output_dir / "venue_availability.xlsx").exists()


def test_cli_run_end_to_end(tmp_path: Path, sample_config_path: Path):
    cache_file = tmp_path / "e2e_cache.json"
    output_dir = tmp_path / "e2e_output"

    result = runner.invoke(
        app,
        [
            "run",
            "--config", str(sample_config_path),
            "--cache-file", str(cache_file),
            "--output-dir", str(output_dir),
            "--mock",
            "--start-date", "2026-10-03",
            "--end-date", "2026-10-31",
            "--export", "both",
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "venue_availability.csv").exists()
    assert (output_dir / "venue_availability.xlsx").exists()


def test_cli_afternoon_only(tmp_path: Path, sample_config_path: Path):
    cache_file = tmp_path / "aft_cache.json"
    output_dir = tmp_path / "aft_output"

    result = runner.invoke(
        app,
        [
            "run",
            "--config", str(sample_config_path),
            "--cache-file", str(cache_file),
            "--output-dir", str(output_dir),
            "--mock",
            "--afternoon-only",
            "--export", "both",
        ],
    )
    assert result.exit_code == 0
    csv_file = output_dir / "venue_availability.csv"
    assert csv_file.exists()
    import pandas as pd
    df = pd.read_csv(csv_file)
    assert "Morning" not in df["TimeSlot"].values
    assert "Afternoon" in df["TimeSlot"].values

