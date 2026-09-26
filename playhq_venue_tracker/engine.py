"""Availability engine and schedule inversion logic."""

from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from playhq_venue_tracker.config import VenueCatalog
from playhq_venue_tracker.models import (
    AppConfig,
    AvailabilityRecord,
    MatchRecord,
    TimeSlotType,
)

logger = logging.getLogger(__name__)

DAY_NAME_TO_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def generate_target_dates(
    start_date: datetime.date,
    end_date: datetime.date,
    target_day: str = "Saturday",
) -> List[datetime.date]:
    """Generate all dates within [start_date, end_date] matching target_day."""
    weekday = DAY_NAME_TO_WEEKDAY.get(target_day.lower().strip(), 5)
    dates: List[datetime.date] = []

    curr = start_date
    one_day = datetime.timedelta(days=1)
    while curr <= end_date:
        if curr.weekday() == weekday:
            dates.append(curr)
        curr += one_day

    logger.debug("Generated %d %ss between %s and %s", len(dates), target_day, start_date, end_date)
    return dates


def parse_club_and_squad(team_name: Optional[str]) -> Tuple[str, str]:
    """Parse club and squad (e.g. 1st XI, U14) from a team string."""
    if not team_name:
        return "Unknown", ""
    import re
    t = team_name.strip()
    if " - " in t:
        parts = t.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    match = re.search(
        r"^(.*?)\s+(\d+(?:st|nd|rd|th)\s+XI|Sunday\s+\d+(?:st|nd|rd|th)\s+XI|U\d+.*|Under\s+\d+.*|Veterans.*|Vets.*)$",
        t,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return t, ""


def infer_competition(grade_name: str) -> str:
    """Infer association/competition from grade title."""
    g = (grade_name or "").lower()
    if any(k in g for k in ["turf", "loc", "wright", "macgibbon", "burt", "mccarthy", "mair"]):
        return "ECA"
    if any(k in g for k in ["mcintosh", "shield", "grade", "bhrdca"]):
        return "BHRDCA"
    return "Cricket"


def infer_ground_tenants(matches: List[MatchRecord], catalog: VenueCatalog) -> None:
    """Infer primary home club, squads, and competition for grounds from fixture data."""
    from collections import defaultdict
    ground_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"clubs": defaultdict(int), "teams": defaultdict(int), "comps": defaultdict(int)}
    )

    for m in matches:
        canon = catalog.resolve_venue(m.venue_name, m.surface_name)
        if not canon:
            continue
        if m.home_team:
            club, squad = parse_club_and_squad(m.home_team)
            ground_stats[canon]["clubs"][club] += 1
            ground_stats[canon]["teams"][squad or club] += 1
        if m.grade_name:
            comp = infer_competition(m.grade_name)
            ground_stats[canon]["comps"][comp] += 1

    for canon, st in ground_stats.items():
        if not st["clubs"]:
            continue
        # Only override or populate if not already set or augmenting
        existing_club = catalog.get_home_club(canon)
        top_club = max(st["clubs"].items(), key=lambda x: x[1])[0]
        top_squads = [sq for sq, _ in sorted(st["teams"].items(), key=lambda x: x[1], reverse=True)[:3]]
        top_comp = max(st["comps"].items(), key=lambda x: x[1])[0]

        catalog.set_tenant_info(
            canon,
            home_club=existing_club or top_club,
            home_teams=catalog.get_home_teams(canon) or top_squads,
            competition=catalog.get_competition(canon) or top_comp,
        )


class AvailabilityEngine:
    """Engine that cross-references fixtures against the master venue catalog and inverts the schedule."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.catalog = VenueCatalog(config.venues)

    def process_fixtures(
        self,
        matches: List[MatchRecord],
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        active_slots: Optional[List[str]] = None,
    ) -> List[AvailabilityRecord]:
        """Invert fixture schedule into complete ground availability records."""
        start = start_date or self.config.date_range.start_date
        end = end_date or self.config.date_range.end_date
        target_dates = generate_target_dates(start, end, self.config.target_day)

        # Infer ground tenants from match home teams
        infer_ground_tenants(matches, self.catalog)

        # 1. Normalize venue names on matches and build index
        booking_index: Dict[Tuple[datetime.date, str, str], List[MatchRecord]] = {}
        unmatched_venues: Set[str] = set()

        for m in matches:
            canonical = self.catalog.resolve_venue(m.venue_name, m.surface_name)
            if canonical:
                m.canonical_venue = canonical
                key = (m.date, canonical, m.time_slot)
                booking_index.setdefault(key, []).append(m)
            else:
                raw_full = f"{m.venue_name} - {m.surface_name}" if m.surface_name else m.venue_name
                unmatched_venues.add(raw_full)

        if unmatched_venues:
            logger.debug(
                "Ignored matches at %d unmonitored venues (e.g. %s)",
                len(unmatched_venues),
                list(unmatched_venues)[:5],
            )

        # 2. Invert schedule: Evaluate every (Date, Venue, TimeSlot)
        records: List[AvailabilityRecord] = []
        canonical_venues = self.catalog.canonical_names
        raw_slots = active_slots or self.config.target_slots or ["Morning", "Afternoon"]
        slots = [s.strip().capitalize() for s in raw_slots]

        for sat_date in target_dates:
            for venue in canonical_venues:
                dist = self.catalog.get_distance(venue)
                sub = self.catalog.get_suburb(venue)
                lat = self.catalog.get_latitude(venue)
                lon = self.catalog.get_longitude(venue)
                h_club = self.catalog.get_home_club(venue)
                h_teams = self.catalog.get_home_teams(venue)
                h_teams_str = ", ".join(h_teams) if h_teams else None
                comp = self.catalog.get_competition(venue)

                for slot in slots:
                    booked_matches = booking_index.get((sat_date, venue, slot), [])
                    if booked_matches:
                        primary = booked_matches[0]
                        details = f"{primary.grade_name} - {primary.home_team} v {primary.away_team}"
                        if len(booked_matches) > 1:
                            details += f" (+{len(booked_matches)-1} other match)"

                        rec = AvailabilityRecord(
                            date=sat_date,
                            day_name=self.config.target_day,
                            time_slot=slot,  # type: ignore
                            venue=venue,
                            distance_km=dist,
                            suburb=sub,
                            latitude=lat,
                            longitude=lon,
                            home_club=h_club,
                            home_teams=h_teams_str,
                            competition=comp,
                            status="BOOKED",
                            grade=primary.grade_name,
                            match_details=details,
                            match_id=primary.match_id,
                            start_time=primary.start_time,
                        )
                    else:
                        rec = AvailabilityRecord(
                            date=sat_date,
                            day_name=self.config.target_day,
                            time_slot=slot,  # type: ignore
                            venue=venue,
                            distance_km=dist,
                            suburb=sub,
                            latitude=lat,
                            longitude=lon,
                            home_club=h_club,
                            home_teams=h_teams_str,
                            competition=comp,
                            status="AVAILABLE",
                            grade=None,
                            match_details=None,
                            match_id=None,
                            start_time=None,
                        )
                    records.append(rec)

        logger.info(
            "Computed availability for %d dates across %d venues (%d slots total)",
            len(target_dates),
            len(canonical_venues),
            len(records),
        )
        return records

    def get_available_grounds(
        self, records: List[AvailabilityRecord], sort_by_distance: bool = True
    ) -> List[AvailabilityRecord]:
        """Return only AVAILABLE ground records, sorted by (Date, Distance ASC, Venue)."""
        avail = [r for r in records if r.status == "AVAILABLE"]
        if sort_by_distance:
            avail.sort(key=lambda r: (r.date, r.distance_km if r.distance_km is not None else 999.0, r.venue))
        else:
            avail.sort(key=lambda r: (r.date, r.venue))
        return avail

    def build_matrix_dataframe(
        self, records: List[AvailabilityRecord], time_slot: str
    ) -> pd.DataFrame:
        """Reshape availability records into a 2D matrix (Rows: Dates, Cols: Venues sorted by distance)."""
        filtered = [r for r in records if r.time_slot == time_slot]
        if not filtered:
            return pd.DataFrame()

        rows_dict: Dict[datetime.date, Dict[str, str]] = {}
        for r in filtered:
            if r.date not in rows_dict:
                rows_dict[r.date] = {}
            if r.status == "AVAILABLE":
                rows_dict[r.date][r.venue] = "AVAILABLE"
            else:
                rows_dict[r.date][r.venue] = f"BOOKED: {r.match_details}"

        df = pd.DataFrame.from_dict(rows_dict, orient="index")
        df.index.name = "Date"

        # Order columns by distance from reference point (closest grounds first)
        def venue_sort_key(v_name: str) -> float:
            d = self.catalog.get_distance(v_name)
            return d if d is not None else 999.0

        cols = [c for c in sorted(self.catalog.canonical_names, key=venue_sort_key) if c in df.columns]
        df = df[cols]
        df = df.sort_index()
        return df
