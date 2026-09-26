"""Data models and schemas for PlayHQ Venue Tracker."""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

TimeSlotType = Literal["Morning", "Afternoon", "Unknown"]
AvailabilityStatus = Literal["AVAILABLE", "BOOKED"]


def parse_time_slot(
    time_str: Optional[str],
    grade_name: Optional[str] = None,
    cutoff_hour: int = 12,
    cutoff_minute: int = 0,
) -> TimeSlotType:
    """Classify a match start time or grade into Morning or Afternoon slot.

    - Morning: Start time strictly before 12:00 PM
    - Afternoon: Start time at or after 12:00 PM
    - If start time is missing or unparseable, infers from grade name keywords.
    """
    if time_str:
        clean_time = time_str.strip().upper()

        # Handle ISO strings: e.g. 2026-10-03T13:00:00Z or 2026-10-03T08:30:00+10:00
        iso_match = re.search(r"T(\d{1,2}):(\d{2})", clean_time)
        if iso_match:
            h, m = int(iso_match.group(1)), int(iso_match.group(2))
            if (h < cutoff_hour) or (h == cutoff_hour and m < cutoff_minute):
                return "Morning"
            return "Afternoon"

        # Handle 12-hour format e.g., "8:30 AM", "1:00 PM", "12:30:00 PM", "12:30pm"
        am_pm_match = re.search(r"(\d{1,2}):?(\d{2})?(?::\d{2})?\s*(AM|PM)", clean_time)
        if am_pm_match:
            h = int(am_pm_match.group(1))
            m = int(am_pm_match.group(2)) if am_pm_match.group(2) else 0
            meridiem = am_pm_match.group(3)
            if meridiem == "AM":
                hour_24 = 0 if h == 12 else h
            else:
                hour_24 = 12 if h == 12 else h + 12
            if (hour_24 < cutoff_hour) or (hour_24 == cutoff_hour and m < cutoff_minute):
                return "Morning"
            return "Afternoon"

        # Handle 24-hour format e.g., "08:30", "13:00", "12:30:00", "9:00"
        time_24_match = re.search(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", clean_time)
        if time_24_match:
            h = int(time_24_match.group(1))
            m = int(time_24_match.group(2))
            if (h < cutoff_hour) or (h == cutoff_hour and m < cutoff_minute):
                return "Morning"
            return "Afternoon"

    # Fallback to grade name hints if time string is absent or ambiguous
    if grade_name:
        grade_lower = grade_name.lower()
        junior_indicators = [
            "junior", "u10", "u11", "u12", "u13", "u14", "u15", "u16",
            "u17", "under 10", "under 12", "under 14", "under 16", "morning"
        ]
        senior_indicators = [
            "senior", "shield", "1st xi", "2nd xi", "3rd xi", "4th xi",
            "veterans", "vet", "open", "afternoon", "turf", "synthetic"
        ]
        if any(ind in grade_lower for ind in junior_indicators):
            return "Morning"
        if any(ind in grade_lower for ind in senior_indicators):
            return "Afternoon"

    return "Unknown"


class MatchRecord(BaseModel):
    """Normalized PlayHQ match fixture record."""

    match_id: str = Field(..., description="Unique identifier for the match")
    date: datetime.date = Field(..., description="Date of the match (YYYY-MM-DD)")
    start_time: Optional[str] = Field(None, description="Start time string (e.g. 13:00, 8:30 AM)")
    time_slot: TimeSlotType = Field("Unknown", description="Morning or Afternoon time slot")
    venue_name: str = Field(..., description="Raw venue name (e.g., Kalang Park)")
    surface_name: Optional[str] = Field(None, description="Playing surface or oval (e.g., Oval 1)")
    grade_name: str = Field(..., description="Competition grade name (e.g., Senior A Turf)")
    home_team: str = Field(..., description="Home team name")
    away_team: str = Field(..., description="Away team name")

    round_name: Optional[str] = Field(None, description="Round name or number")
    status: Optional[str] = Field(None, description="Game status (e.g., UPCOMING, COMPLETE)")
    canonical_venue: Optional[str] = Field(None, description="Normalized master venue name")
    source_url: Optional[str] = Field(None, description="URL where fixture was found")

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> datetime.date:
        if isinstance(v, datetime.date):
            return v
        if isinstance(v, datetime.datetime):
            return v.date()
        if isinstance(v, str):
            clean_str = v.split("T")[0].strip()
            return datetime.date.fromisoformat(clean_str)
        raise ValueError(f"Cannot parse date from: {v}")

    @model_validator(mode="after")
    def populate_time_slot(self) -> MatchRecord:
        if self.time_slot == "Unknown":
            self.time_slot = parse_time_slot(self.start_time, self.grade_name)
        return self


class AvailabilityRecord(BaseModel):
    """Record describing ground availability for a specific date and time slot."""

    date: datetime.date = Field(..., description="Date of the audit slot")
    day_name: str = Field("Saturday", description="Name of the day of the week")
    time_slot: Literal["Morning", "Afternoon"] = Field(..., description="Slot name")
    venue: str = Field(..., description="Master canonical venue name")
    distance_km: Optional[float] = Field(None, description="Haversine distance from reference club in km")
    suburb: Optional[str] = Field(None, description="Suburb of venue")
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")
    home_club: Optional[str] = Field(None, description="Primary home club / tenant")
    home_teams: Optional[str] = Field(None, description="Home squads/teams using this ground")
    competition: Optional[str] = Field(None, description="Home competition (e.g. BHRDCA, ECA)")
    status: AvailabilityStatus = Field(..., description="AVAILABLE or BOOKED")
    grade: Optional[str] = Field(None, description="Grade occupying the venue if BOOKED")
    match_details: Optional[str] = Field(None, description="Formatted summary of booked match")
    match_id: Optional[str] = Field(None, description="Match ID if BOOKED")
    start_time: Optional[str] = Field(None, description="Start time if BOOKED")


class VenueConfig(BaseModel):
    """Configuration for a single audit venue and its name aliases."""

    name: str = Field(..., description="Canonical venue/ground name")
    aliases: List[str] = Field(default_factory=list, description="Alternative names/aliases")
    distance_km: Optional[float] = Field(None, description="Haversine distance in km from reference point")
    suburb: Optional[str] = Field(None, description="Suburb of the ground")
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")
    home_club: Optional[str] = Field(None, description="Primary home club / tenant")
    home_teams: List[str] = Field(default_factory=list, description="Home squads/teams using this ground")
    competition: Optional[str] = Field(None, description="Home competition (e.g. BHRDCA, ECA)")


class DateRangeConfig(BaseModel):
    """Start and end dates for availability checking."""

    start_date: datetime.date = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: datetime.date = Field(..., description="End date (YYYY-MM-DD)")

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> datetime.date:
        if isinstance(v, datetime.date):
            return v
        if isinstance(v, datetime.datetime):
            return v.date()
        if isinstance(v, str):
            return datetime.date.fromisoformat(v.strip())
        raise ValueError(f"Cannot parse date: {v}")

    @model_validator(mode="after")
    def validate_range(self) -> DateRangeConfig:
        if self.start_date > self.end_date:
            raise ValueError(f"start_date ({self.start_date}) cannot be after end_date ({self.end_date})")
        return self


class TimeSlotSetting(BaseModel):
    """Definition and rules for a time slot."""

    cutoff: str = Field("12:00", description="Cutoff time in 24hr format HH:MM")
    description: Optional[str] = Field(None, description="Description of the slot")


class SeasonConfig(BaseModel):
    """Configuration for a competition season to scrape."""

    name: str = Field(..., description="Competition or season description")
    id: str = Field(..., description="PlayHQ season ID (e.g. 77fbfe27)")


class AppConfig(BaseModel):
    """Top-level configuration schema for PlayHQ Venue Tracker."""

    association_url: Optional[str] = Field(
        None, description="Base URL of association or competition on PlayHQ"
    )
    seasons: List[SeasonConfig] = Field(
        default_factory=list, description="Target competition seasons to scrape"
    )
    fixture_urls: List[str] = Field(
        default_factory=list, description="Explicit list of grade fixture URLs"
    )
    date_range: DateRangeConfig = Field(..., description="Target date range")
    target_day: str = Field("Saturday", description="Day of week to audit (e.g. Saturday)")
    target_slots: Optional[List[str]] = Field(
        None, description="Specific time slots to audit (e.g. ['Afternoon'] to exclude mornings)"
    )
    time_slots: Dict[str, TimeSlotSetting] = Field(
        default_factory=lambda: {
            "Morning": TimeSlotSetting(cutoff="12:00", description="Start times before 12:00 PM"),
            "Afternoon": TimeSlotSetting(cutoff="12:00", description="Start times 12:00 PM and later"),
        }
    )
    venues: List[VenueConfig] = Field(
        default_factory=list, description="List of target venues with aliases"
    )

    @model_validator(mode="after")
    def populate_target_slots(self) -> AppConfig:
        if not self.target_slots:
            # Default to slots defined in time_slots dictionary
            self.target_slots = [k.capitalize() for k in self.time_slots.keys()]
        else:
            # Clean and normalize names e.g. "afternoon" -> "Afternoon"
            self.target_slots = [s.strip().capitalize() for s in self.target_slots]
        return self
