"""Exporters for generating Excel workbooks, CSV summaries, and Rich terminal tables."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import List, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table

from playhq_venue_tracker.engine import AvailabilityEngine
from playhq_venue_tracker.models import AvailabilityRecord, MatchRecord

logger = logging.getLogger(__name__)


# Styling palettes for Excel
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
DATE_FILL = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
DATE_FONT = Font(name="Calibri", size=10, bold=True, color="1F2937")

AVAIL_FILL = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")
AVAIL_FONT = Font(name="Calibri", size=10, bold=True, color="155724")

BOOKED_FILL = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
BOOKED_FONT = Font(name="Calibri", size=9, bold=False, color="721C24")

THIN_BORDER = Border(
    left=Side(style="thin", color="D1D5DB"),
    right=Side(style="thin", color="D1D5DB"),
    top=Side(style="thin", color="D1D5DB"),
    bottom=Side(style="thin", color="D1D5DB"),
)


def export_csv(records: List[AvailabilityRecord], output_path: str | Path) -> Path:
    """Export flat availability records and dedicated available grounds to CSV files."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    sorted_records = sorted(
        records,
        key=lambda r: (r.date, r.distance_km if r.distance_km is not None else 999.0, r.venue)
    )

    rows = []
    avail_rows = []
    for r in sorted_records:
        row_dict = {
            "Date": r.date.isoformat(),
            "Day": r.day_name,
            "TimeSlot": r.time_slot,
            "DistanceKm": r.distance_km if r.distance_km is not None else "",
            "Venue": r.venue,
            "Suburb": r.suburb or "",
            "Latitude": r.latitude if r.latitude is not None else "",
            "Longitude": r.longitude if r.longitude is not None else "",
            "HomeClub": r.home_club or "",
            "HomeTeams": r.home_teams or "",
            "Competition": r.competition or "",
            "Status": r.status,
            "Grade": r.grade or "",
            "MatchDetails": r.match_details or "",
        }
        rows.append(row_dict)
        if r.status == "AVAILABLE":
            avail_rows.append({
                "Date": r.date.isoformat(),
                "Day": r.day_name,
                "TimeSlot": r.time_slot,
                "DistanceKm": r.distance_km if r.distance_km is not None else "",
                "Venue": r.venue,
                "Suburb": r.suburb or "",
                "Latitude": r.latitude if r.latitude is not None else "",
                "Longitude": r.longitude if r.longitude is not None else "",
                "HomeClub": r.home_club or "",
                "HomeTeams": r.home_teams or "",
                "Competition": r.competition or "",
            })

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info("Successfully exported CSV summary to %s (%d rows)", path, len(df))

    # Also export dedicated available_grounds.csv (available only, date & distance order)
    avail_path = path.parent / "available_grounds.csv"
    df_avail = pd.DataFrame(avail_rows)
    df_avail.to_csv(avail_path, index=False, encoding="utf-8")
    logger.info("Successfully exported available grounds to %s (%d rows)", avail_path, len(df_avail))

    return path


def export_excel(
    engine: AvailabilityEngine,
    records: List[AvailabilityRecord],
    raw_matches: List[MatchRecord],
    output_path: str | Path,
) -> Path:
    """Export availability matrices and available grounds to a styled multi-sheet Excel workbook."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    existing_slots = set(r.time_slot for r in records)

    # Sheet 1: Available Grounds (Closest First)
    ws_avail = wb.active
    ws_avail.title = "Available Grounds"
    _populate_available_grounds_sheet(ws_avail, records)

    # Sheet 2: Afternoon Seniors Matrix
    if "Afternoon" in existing_slots:
        ws_afternoon = wb.create_sheet(title="Afternoon Seniors")
        df_afternoon = engine.build_matrix_dataframe(records, time_slot="Afternoon")
        _populate_matrix_sheet(ws_afternoon, df_afternoon, "Afternoon Senior Ground Availability Matrix (Distance Order)")

    # Sheet 3: Morning Juniors Matrix (if audited)
    if "Morning" in existing_slots:
        ws_morning = wb.create_sheet(title="Morning Juniors")
        df_morning = engine.build_matrix_dataframe(records, time_slot="Morning")
        _populate_matrix_sheet(ws_morning, df_morning, "Morning Junior Ground Availability Matrix (Distance Order)")

    # Sheet 4: Raw Match Dump
    ws_raw = wb.create_sheet(title="Raw Match Dump")
    _populate_raw_matches_sheet(ws_raw, raw_matches)

    wb.save(path)
    logger.info("Successfully exported formatted Excel matrix workbook to %s", path)
    return path


def _populate_available_grounds_sheet(
    ws: openpyxl.worksheet.worksheet.Worksheet, records: List[AvailabilityRecord]
) -> None:
    """Populate dedicated sheet of free/available grounds ordered by Date and Distance."""
    ws.views.sheetView[0].showGridLines = True

    avail = [r for r in records if r.status == "AVAILABLE"]
    avail.sort(key=lambda r: (r.date, r.distance_km if r.distance_km is not None else 999.0, r.venue))

    headers = [
        "Date",
        "Day",
        "Time Slot",
        "Distance from LCC (km)",
        "Ground / Venue",
        "Suburb",
        "Latitude",
        "Longitude",
        "Home Club",
        "Home Teams",
        "Competition",
    ]

    # Title row
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title_cell = ws.cell(row=1, column=1, value="Available / Free Grounds (Ordered by Date & Distance from Laburnum CC)")
    title_cell.font = Font(name="Calibri", size=13, bold=True, color="1F4E79")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    # Header Row (Row 2)
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=col_idx, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
    ws.row_dimensions[2].height = 24

    current_date = None
    date_group_color = False

    for row_idx, r in enumerate(avail, start=3):
        if r.date != current_date:
            current_date = r.date
            date_group_color = not date_group_color

        row_fill = PatternFill(start_color="F0FDF4" if date_group_color else "FFFFFF", fill_type="solid")
        date_str = r.date.strftime("%Y-%m-%d")

        row_vals = [
            date_str,
            r.day_name,
            r.time_slot,
            f"{r.distance_km:.2f} km" if r.distance_km is not None else "N/A",
            r.venue,
            r.suburb or "",
            f"{r.latitude:.5f}" if r.latitude is not None else "",
            f"{r.longitude:.5f}" if r.longitude is not None else "",
            r.home_club or "",
            r.home_teams or "",
            r.competition or "",
        ]

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = THIN_BORDER
            cell.fill = row_fill
            align = "center" if col_idx in (1, 2, 3, 4, 7, 8, 11) else "left"
            cell.alignment = Alignment(horizontal=align, vertical="center")
        ws.row_dimensions[row_idx].height = 20

    col_widths = [14, 12, 14, 24, 38, 20, 14, 14, 24, 30, 15]
    for col_idx, width in enumerate(col_widths, start=1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{max(3, len(avail) + 2)}"
    ws.freeze_panes = "A3"


def _populate_matrix_sheet(ws: openpyxl.worksheet.worksheet.Worksheet, df: pd.DataFrame, title: str) -> None:
    """Style and populate a 2D availability matrix sheet in openpyxl."""
    ws.views.sheetView[0].showGridLines = True

    # Title row
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(df.columns) + 1)
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    # Header Row (Row 2)
    ws.cell(row=2, column=1, value="Date")
    ws.cell(row=2, column=1).fill = HEADER_FILL
    ws.cell(row=2, column=1).font = HEADER_FONT
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=2, column=1).border = THIN_BORDER

    for col_idx, col_name in enumerate(df.columns, start=2):
        cell = ws.cell(row=2, column=col_idx, value=col_name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER

    ws.row_dimensions[2].height = 26

    # Data Rows
    current_row = 3
    for date_val, row_data in df.iterrows():
        # Date cell
        date_str = date_val.strftime("%Y-%m-%d (%a)") if hasattr(date_val, "strftime") else str(date_val)
        d_cell = ws.cell(row=current_row, column=1, value=date_str)
        d_cell.fill = DATE_FILL
        d_cell.font = DATE_FONT
        d_cell.alignment = Alignment(horizontal="center", vertical="center")
        d_cell.border = THIN_BORDER

        # Ground cells
        for col_idx, col_name in enumerate(df.columns, start=2):
            val = str(row_data.get(col_name, "AVAILABLE"))
            c = ws.cell(row=current_row, column=col_idx, value=val)
            c.border = THIN_BORDER
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            if val == "AVAILABLE":
                c.fill = AVAIL_FILL
                c.font = AVAIL_FONT
            else:
                c.fill = BOOKED_FILL
                c.font = BOOKED_FONT

        ws.row_dimensions[current_row].height = 28
        current_row += 1

    # Auto-adjust column widths
    ws.column_dimensions["A"].width = 18
    for col_idx, col_name in enumerate(df.columns, start=2):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max(24, len(str(col_name)) + 6)


def _populate_raw_matches_sheet(ws: openpyxl.worksheet.worksheet.Worksheet, matches: List[MatchRecord]) -> None:
    """Populate raw fixtures dump sheet."""
    ws.views.sheetView[0].showGridLines = True

    headers = [
        "Match ID",
        "Date",
        "Start Time",
        "Time Slot",
        "Canonical Venue",
        "Raw Venue",
        "Surface",
        "Grade",
        "Home Team",
        "Away Team",
        "Status",
    ]

    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER

    ws.row_dimensions[1].height = 24

    for row_idx, m in enumerate(matches, start=2):
        row_vals = [
            m.match_id,
            m.date.isoformat(),
            m.start_time or "",
            m.time_slot,
            m.canonical_venue or "",
            m.venue_name,
            m.surface_name or "",
            m.grade_name,
            m.home_team,
            m.away_team,
            m.status or "",
        ]
        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = THIN_BORDER
            cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row_idx].height = 20

    # Auto-adjust column widths
    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = 20
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(2, len(matches) + 1)}"


def print_rich_summary(
    records: List[AvailabilityRecord],
    console: Optional[Console] = None,
    max_grounds_per_date: int = 8,
) -> None:
    """Print an immediate color-coded summary table of available grounds to console in distance order."""
    console = console or Console()

    # Calculate statistics
    total_slots = len(records)
    avail_records = [r for r in records if r.status == "AVAILABLE"]
    booked_records = [r for r in records if r.status == "BOOKED"]

    # Group by (date, time_slot)
    dates = sorted(list(set(r.date for r in records)))
    slots = sorted(list(set(r.time_slot for r in records)))

    console.print()
    console.rule("[bold cyan]PlayHQ Cricket Ground Availability Summary (Distance from LCC)[/bold cyan]")
    console.print(
        f"Audited [bold]{len(dates)}[/bold] target Saturdays across [bold]{len(set(r.venue for r in records))}[/bold] grounds.\n"
        f"Total Ground Slots: [bold green]{len(avail_records)} Available[/bold green] | "
        f"[bold red]{len(booked_records)} Booked[/bold red] (Total: {total_slots})"
    )
    console.print()

    table = Table(
        title="Saturday Available Grounds (Ordered by Distance from Laburnum CC)",
        box=box.ROUNDED,
        header_style="bold bright_white on blue",
        expand=True,
    )

    table.add_column("Date", justify="center", style="bold cyan", no_wrap=True, width=16)
    table.add_column("Slot", justify="center", style="bold magenta", no_wrap=True, width=11)
    table.add_column("Free", justify="center", style="bold green", no_wrap=True, width=7)
    table.add_column("Closest Available Grounds (Distance Order)", justify="left")

    for d in dates:
        for slot in slots:
            slot_avail = [
                r for r in records
                if r.date == d and r.time_slot == slot and r.status == "AVAILABLE"
            ]
            if not slot_avail:
                continue

            # Sort by distance ASC, then venue name
            slot_avail.sort(
                key=lambda r: (r.distance_km if r.distance_km is not None else 999.0, r.venue)
            )

            lines = []
            for idx, r in enumerate(slot_avail[:max_grounds_per_date], start=1):
                dist_str = f"[bold green]{r.distance_km:.2f} km[/bold green]" if r.distance_km is not None else "[dim]N/A[/dim]"
                meta_parts = []
                if r.home_club:
                    comp_tag = f" [{r.competition}]" if r.competition else ""
                    meta_parts.append(f"[bold yellow]{r.home_club}{comp_tag}[/bold yellow]")
                if r.suburb:
                    meta_parts.append(f"[dim]{r.suburb}[/dim]")
                meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
                lines.append(f"[bold]{idx}.[/bold] {r.venue} ({dist_str}){meta_str}")

            remaining = len(slot_avail) - max_grounds_per_date
            if remaining > 0:
                lines.append(f"[italic cyan]... +{remaining} more free grounds (see output/available_grounds.csv)[/italic cyan]")

            table.add_row(
                d.strftime("%Y-%m-%d (%a)"),
                slot,
                str(len(slot_avail)),
                "\n".join(lines),
            )

    console.print(table)
    console.print()
