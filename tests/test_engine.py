"""Unit tests for availability engine and inversion logic."""

import datetime
import pytest

from playhq_venue_tracker.config import VenueCatalog
from playhq_venue_tracker.engine import AvailabilityEngine, generate_target_dates
from playhq_venue_tracker.models import AppConfig, MatchRecord, VenueConfig


def test_generate_target_dates_saturdays():
    start = datetime.date(2026, 10, 3)
    end = datetime.date(2027, 2, 27)

    dates = generate_target_dates(start, end, target_day="Saturday")
    assert len(dates) == 22
    assert dates[0] == datetime.date(2026, 10, 3)
    assert dates[-1] == datetime.date(2027, 2, 27)
    for d in dates:
        assert d.weekday() == 5  # Saturday


def test_venue_catalog_resolution():
    catalog = VenueCatalog([
        VenueConfig(
            name="Kalang Park 1",
            aliases=["Kalang Park Oval 1", "Kalang Park #1", "Kalang Park (Oval 1)"]
        ),
        VenueConfig(
            name="Kalang Park 2",
            aliases=["Kalang Park Oval 2", "Kalang Park #2"]
        ),
        VenueConfig(
            name="Box Hill City Oval",
            aliases=["City Oval", "Box Hill Oval"]
        )
    ])

    # Direct canonical
    assert catalog.resolve_venue("Kalang Park 1") == "Kalang Park 1"
    # Configured alias
    assert catalog.resolve_venue("Kalang Park Oval 1") == "Kalang Park 1"
    assert catalog.resolve_venue("Kalang Park #1") == "Kalang Park 1"
    # Combined venue and surface
    assert catalog.resolve_venue("Kalang Park", "Oval 1") == "Kalang Park 1"
    assert catalog.resolve_venue("Kalang Park", "Oval 2") == "Kalang Park 2"
    # Alias for Box Hill
    assert catalog.resolve_venue("City Oval") == "Box Hill City Oval"
    assert catalog.resolve_venue("Box Hill Oval", "Main Ground") == "Box Hill City Oval"
    # Unmonitored venue
    assert catalog.resolve_venue("Random Park Oval 99") is None


def test_availability_engine_inversion(app_config: AppConfig):
    engine = AvailabilityEngine(app_config)

    # 1 booked match on 2026-10-03 Morning at Kalang Park 1
    sample_match = MatchRecord(
        match_id="m-test-1",
        date=datetime.date(2026, 10, 3),
        start_time="08:30",
        time_slot="Morning",
        venue_name="Kalang Park",
        surface_name="Oval 1",
        grade_name="Under 14 A",
        home_team="Box Hill CC",
        away_team="Surrey Hills CC",
    )

    records = engine.process_fixtures([sample_match])

    # Check the booked slot
    kp1_morning = next(
        r for r in records
        if r.date == datetime.date(2026, 10, 3)
        and r.venue == "Kalang Park 1"
        and r.time_slot == "Morning"
    )
    assert kp1_morning.status == "BOOKED"
    assert kp1_morning.grade == "Under 14 A"
    assert "Box Hill CC v Surrey Hills CC" in (kp1_morning.match_details or "")

    # Check the Afternoon slot on the same day (should be AVAILABLE)
    kp1_afternoon = next(
        r for r in records
        if r.date == datetime.date(2026, 10, 3)
        and r.venue == "Kalang Park 1"
        and r.time_slot == "Afternoon"
    )
    assert kp1_afternoon.status == "AVAILABLE"
    assert kp1_afternoon.grade is None
    assert kp1_afternoon.match_details is None

    # Check an unbooked venue (Surrey Park 1)
    sp1_morning = next(
        r for r in records
        if r.date == datetime.date(2026, 10, 3)
        and r.venue == "Surrey Park 1"
        and r.time_slot == "Morning"
    )
    assert sp1_morning.status == "AVAILABLE"


def test_matrix_dataframe_reshaping(app_config: AppConfig, mock_matches: list[MatchRecord]):
    engine = AvailabilityEngine(app_config)
    records = engine.process_fixtures(mock_matches)

    df_afternoon = engine.build_matrix_dataframe(records, time_slot="Afternoon")
    assert not df_afternoon.empty
    assert len(df_afternoon) == 22  # 22 Saturdays
    # Monitored venues should be columns
    for v in ["Kalang Park 1", "Kalang Park 2", "Surrey Park 1", "Box Hill City Oval"]:
        assert v in df_afternoon.columns


def test_availability_engine_afternoon_only(app_config: AppConfig, mock_matches: list[MatchRecord]):
    engine = AvailabilityEngine(app_config)
    records = engine.process_fixtures(mock_matches, active_slots=["Afternoon"])

    assert len(records) > 0
    # Every record must be Afternoon
    for r in records:
        assert r.time_slot == "Afternoon"
    # Ensure no Morning records exist
    assert not any(r.time_slot == "Morning" for r in records)


def test_kalang_park_afternoon_booked():
    """Verify that a 12:30:00 match at Kalang Park marks the Afternoon slot as BOOKED."""
    config = AppConfig(
        date_range={"start_date": "2026-10-03", "end_date": "2026-10-03"},
        target_slots=["Afternoon"],
        venues=[
            VenueConfig(name="Kalang Park", aliases=["Kalang Park", "Kalang Park - Kalang Park"])
        ],
    )
    engine = AvailabilityEngine(config)

    match = MatchRecord(
        match_id="kalang-match-1",
        date=datetime.date(2026, 10, 3),
        start_time="12:30:00",
        venue_name="Kalang Park",
        surface_name="Kalang Park",
        grade_name="A Grade (80 Overs, 12 Players)",
        home_team="Laburnum - 2nd XI",
        away_team="East Burwood - 2nd XI",
    )

    records = engine.process_fixtures([match], active_slots=["Afternoon"])
    assert len(records) == 1
    rec = records[0]
    assert rec.venue == "Kalang Park"
    assert rec.date == datetime.date(2026, 10, 3)
    assert rec.time_slot == "Afternoon"
    assert rec.status == "BOOKED"
    assert "Laburnum - 2nd XI v East Burwood - 2nd XI" in rec.match_details
    assert rec.grade == "A Grade (80 Overs, 12 Players)"


def test_tenant_inference_and_propagation():
    """Verify that tenant club, squads, and competition are inferred and attached."""
    from playhq_venue_tracker.engine import infer_competition, parse_club_and_squad

    # Test squad parser
    club, squad = parse_club_and_squad("Blackburn - 1st XI")
    assert club == "Blackburn"
    assert squad == "1st XI"

    club2, squad2 = parse_club_and_squad("Surrey Hills Sunday 1st XI")
    assert club2 == "Surrey Hills"
    assert squad2 == "Sunday 1st XI"

    # Test competition inference
    assert infer_competition("Compare & Connect Dorothy McIntosh Shield") == "BHRDCA"
    assert infer_competition("02. Wright Shield") == "ECA"

    # Test engine attachment
    config = AppConfig(
        date_range={"start_date": "2026-10-03", "end_date": "2026-10-03"},
        target_slots=["Afternoon"],
        venues=[
            VenueConfig(name="Kalang Park", aliases=["Kalang Park"])
        ],
    )
    engine = AvailabilityEngine(config)
    match = MatchRecord(
        match_id="kalang-match-1",
        date=datetime.date(2026, 10, 3),
        start_time="12:30:00",
        venue_name="Kalang Park",
        surface_name="Kalang Park",
        grade_name="3. Compare & Connect Dorothy McIntosh Shield",
        home_team="Laburnum - 1st XI",
        away_team="East Burwood - 1st XI",
    )
    records = engine.process_fixtures([match], active_slots=["Afternoon"])
    assert len(records) == 1
    rec = records[0]
    assert rec.home_club == "Laburnum"
    assert "1st XI" in rec.home_teams
    assert rec.competition == "BHRDCA"



