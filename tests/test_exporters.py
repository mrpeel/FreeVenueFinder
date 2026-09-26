"""Unit tests for Excel, CSV, and terminal summary exporters."""

from pathlib import Path
import openpyxl
import pandas as pd
import pytest

from playhq_venue_tracker.engine import AvailabilityEngine
from playhq_venue_tracker.exporters import export_csv, export_excel, print_rich_summary
from playhq_venue_tracker.models import AppConfig, MatchRecord


def test_export_csv(tmp_path: Path, app_config: AppConfig, mock_matches: list[MatchRecord]):
    engine = AvailabilityEngine(app_config)
    records = engine.process_fixtures(mock_matches)

    csv_path = tmp_path / "test_availability.csv"
    export_csv(records, csv_path)

    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    for col in ["Date", "TimeSlot", "Venue", "Status", "Grade", "MatchDetails", "DistanceKm", "Suburb"]:
        assert col in df.columns
    assert len(df) == len(records)
    assert "AVAILABLE" in df["Status"].values
    assert "BOOKED" in df["Status"].values

    # Verify dedicated available_grounds.csv was also created
    avail_csv = tmp_path / "available_grounds.csv"
    assert avail_csv.exists()
    df_avail = pd.read_csv(avail_csv)
    assert len(df_avail) > 0
    assert "DistanceKm" in df_avail.columns


def test_export_excel(tmp_path: Path, app_config: AppConfig, mock_matches: list[MatchRecord]):
    engine = AvailabilityEngine(app_config)
    records = engine.process_fixtures(mock_matches)

    xlsx_path = tmp_path / "test_matrix.xlsx"
    export_excel(engine, records, mock_matches, xlsx_path)

    assert xlsx_path.exists()
    wb = openpyxl.load_workbook(xlsx_path)

    # Check sheet names
    expected_sheets = ["Available Grounds", "Afternoon Seniors", "Morning Juniors", "Raw Match Dump"]
    assert wb.sheetnames == expected_sheets

    # Sheet 1: Available Grounds
    ws_avail = wb["Available Grounds"]
    assert "Available" in str(ws_avail.cell(row=1, column=1).value)

    # Sheet 2: Afternoon Seniors
    ws_afternoon = wb["Afternoon Seniors"]
    assert "Afternoon Senior Ground Availability Matrix" in str(ws_afternoon.cell(row=1, column=1).value)
    assert ws_afternoon.cell(row=2, column=1).value == "Date"

    # Verify styling on available and booked cells
    found_avail = False
    found_booked = False
    for r in range(3, ws_afternoon.max_row + 1):
        for c in range(2, ws_afternoon.max_column + 1):
            val = str(ws_afternoon.cell(row=r, column=c).value)
            if val == "AVAILABLE":
                found_avail = True
                assert ws_afternoon.cell(row=r, column=c).fill.start_color.rgb == "00D4EDDA"
            elif val.startswith("BOOKED:"):
                found_booked = True
                assert ws_afternoon.cell(row=r, column=c).fill.start_color.rgb == "00F8D7DA"

    assert found_avail
    assert found_booked

    # Sheet 3: Raw Match Dump
    ws_raw = wb["Raw Match Dump"]
    assert ws_raw.cell(row=1, column=1).value == "Match ID"
    assert ws_raw.max_row == len(mock_matches) + 1


def test_print_rich_summary(app_config: AppConfig, mock_matches: list[MatchRecord]):
    engine = AvailabilityEngine(app_config)
    records = engine.process_fixtures(mock_matches)
    # Ensure print_rich_summary executes cleanly without raising any exceptions
    print_rich_summary(records)
