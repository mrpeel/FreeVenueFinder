"""Realistic mock PlayHQ data and Next.js HTML payloads for testing and offline runs."""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

from playhq_venue_tracker.models import MatchRecord


def generate_mock_fixtures_dataset() -> List[MatchRecord]:
    """Generate realistic PlayHQ match fixtures across the 2026-10-03 to 2027-02-27 season."""
    matches: List[MatchRecord] = []

    # Dates: Every Saturday between 2026-10-03 and 2027-02-27
    saturdays = []
    curr = datetime.date(2026, 10, 3)
    end = datetime.date(2027, 2, 27)
    while curr <= end:
        saturdays.append(curr)
        curr += datetime.timedelta(days=7)

    # Teams
    teams = [
        "Box Hill CC", "Surrey Hills CC", "Mont Albert CC",
        "Deepdene Bears CC", "Richmond City CC", "Kew CC",
        "North Balwyn CC", "Hawthorn CC"
    ]

    match_counter = 1000

    for idx, sat in enumerate(saturdays):
        # 1. Morning Junior Matches (8:30 AM)
        # Kalang Park 1 booked on alternating weeks
        if idx % 2 == 0:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="08:30",
                    time_slot="Morning",
                    venue_name="Kalang Park",
                    surface_name="Oval 1",
                    grade_name="Under 14 A",
                    home_team=teams[idx % len(teams)],
                    away_team=teams[(idx + 1) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # Surrey Park 1 booked on Morning
        if idx % 3 != 0:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="09:00",
                    time_slot="Morning",
                    venue_name="Surrey Park",
                    surface_name="Oval 1",
                    grade_name="Under 12 B",
                    home_team=teams[(idx + 2) % len(teams)],
                    away_team=teams[(idx + 3) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # Elgar Park North booked in Morning
        if idx % 2 == 1:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="08:45",
                    time_slot="Morning",
                    venue_name="Elgar Park",
                    surface_name="Oval 1",  # Alias for Elgar Park North
                    grade_name="Under 16 A",
                    home_team=teams[(idx + 4) % len(teams)],
                    away_team=teams[(idx + 5) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # 2. Afternoon Senior Matches (1:00 PM / 13:00)
        # Box Hill City Oval booked on most Saturdays
        if idx % 4 != 0:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="13:00",
                    time_slot="Afternoon",
                    venue_name="Box Hill City Oval",
                    surface_name="Main Oval",
                    grade_name="Senior 1st XI Turf",
                    home_team="Box Hill CC",
                    away_team=teams[(idx + 1) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # Kalang Park 2 booked on Afternoon
        if idx % 2 == 1:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="13:15",
                    time_slot="Afternoon",
                    venue_name="Kalang Park",
                    surface_name="Oval 2",
                    grade_name="Senior B Synthetic",
                    home_team=teams[(idx + 2) % len(teams)],
                    away_team=teams[(idx + 6) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # Mont Albert Reserve booked in Afternoon
        if idx % 3 == 1:
            matches.append(
                MatchRecord(
                    match_id=f"m-{match_counter}",
                    date=sat,
                    start_time="12:30",
                    time_slot="Afternoon",
                    venue_name="Mont Albert Reserve",
                    surface_name="Oval 1",
                    grade_name="Veterans Division 1",
                    home_team="Mont Albert CC",
                    away_team=teams[(idx + 3) % len(teams)],
                    round_name=f"Round {idx + 1}",
                )
            )
            match_counter += 1

        # An unmonitored venue (e.g. Glen Iris Park) to test catalog filtering
        matches.append(
            MatchRecord(
                match_id=f"m-{match_counter}",
                date=sat,
                start_time="13:00",
                time_slot="Afternoon",
                venue_name="Glen Iris Park",
                surface_name="Oval 3",
                grade_name="Senior C Synthetic",
                home_team="Glen Iris CC",
                away_team="Ashburton CC",
                round_name=f"Round {idx + 1}",
            )
        )
        match_counter += 1

    return matches


def build_mock_next_data_payload() -> Dict[str, Any]:
    """Build a realistic Next.js __NEXT_DATA__ dehydrated state payload."""
    matches = generate_mock_fixtures_dataset()

    games_payload = []
    for m in matches:
        games_payload.append({
            "id": m.match_id,
            "date": m.date.isoformat(),
            "dates": [m.date.isoformat()],
            "home": {
                "name": m.home_team,
                "id": f"team-home-{m.match_id}",
            },
            "away": {
                "name": m.away_team,
                "id": f"team-away-{m.match_id}",
            },
            "allocation": {
                "time": m.start_time,
                "dateTimeList": [
                    {
                        "date": m.date.isoformat(),
                        "time": m.start_time,
                    }
                ],
                "court": {
                    "id": f"court-{m.match_id}",
                    "name": m.surface_name,
                    "venue": {
                        "id": f"venue-{m.match_id}",
                        "name": m.venue_name,
                    },
                },
            },
            "grade": {
                "name": m.grade_name,
                "id": "grade-123",
            },
            "status": {
                "name": "UPCOMING",
                "value": "UPCOMING",
            },
        })

    next_data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["discoverGradeFixture", "grade-123"],
                            "state": {
                                "data": {
                                    "discoverGradeFixture": {
                                        "id": "grade-123",
                                        "name": "ECA Saturday Fixtures",
                                        "games": games_payload,
                                    }
                                }
                            },
                        }
                    ]
                }
            }
        },
        "page": "/cricket-australia/org/[orgSlug]/[compSlug]/fixtures-and-ladders",
        "buildId": "playhq-prod-build-2026",
    }
    return next_data


def build_mock_html_page() -> str:
    """Generate a realistic Next.js HTML page with embedded __NEXT_DATA__."""
    next_data = build_mock_next_data_payload()
    json_str = json.dumps(next_data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>PlayHQ Cricket Fixtures & Ladders</title>
</head>
<body>
  <div id="__next">
    <main>
      <h1>Eastern Cricket Association - Summer 2026/27</h1>
      <div class="fixtures-container">
        <!-- Rendered client components -->
      </div>
    </main>
  </div>
  <script id="__NEXT_DATA__" type="application/json">{json_str}</script>
</body>
</html>"""
    return html
