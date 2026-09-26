"""Configuration loader and venue catalog manager for PlayHQ Venue Tracker."""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Dict, List, Optional, Set
import yaml

from playhq_venue_tracker.models import AppConfig, VenueConfig

logger = logging.getLogger(__name__)


def clean_string(val: Optional[str]) -> str:
    """Normalize a string for case-insensitive, punctuation-agnostic comparison."""
    if not val:
        return ""
    # Lowercase, replace non-alphanumeric with spaces, collapse spaces
    s = val.lower()
    s = re.sub(r"[#\-_/\\(),.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class VenueCatalog:
    """Catalog of canonical grounds/venues with alias resolution."""

    def __init__(self, venues: List[VenueConfig]):
        self.venues = venues
        self._canonical_names: List[str] = [v.name for v in venues]
        self._distance_map: Dict[str, Optional[float]] = {v.name: v.distance_km for v in venues}
        self._suburb_map: Dict[str, Optional[str]] = {v.name: v.suburb for v in venues}
        self._latitude_map: Dict[str, Optional[float]] = {v.name: v.latitude for v in venues}
        self._longitude_map: Dict[str, Optional[float]] = {v.name: v.longitude for v in venues}
        self._home_club_map: Dict[str, Optional[str]] = {v.name: v.home_club for v in venues}
        self._home_teams_map: Dict[str, List[str]] = {v.name: v.home_teams or [] for v in venues}
        self._competition_map: Dict[str, Optional[str]] = {v.name: v.competition for v in venues}
        self._alias_map: Dict[str, str] = {}
        self._init_alias_map()

    def get_distance(self, canon_name: str) -> Optional[float]:
        """Return distance in km from reference point for a canonical venue name."""
        return self._distance_map.get(canon_name)

    def get_suburb(self, canon_name: str) -> Optional[str]:
        """Return suburb for a canonical venue name."""
        return self._suburb_map.get(canon_name)

    def get_latitude(self, canon_name: str) -> Optional[float]:
        """Return latitude for a canonical venue name."""
        return self._latitude_map.get(canon_name)

    def get_longitude(self, canon_name: str) -> Optional[float]:
        """Return longitude for a canonical venue name."""
        return self._longitude_map.get(canon_name)

    def set_coordinates(self, canon_name: str, lat: float, lon: float) -> None:
        """Set latitude and longitude for a canonical venue."""
        self._latitude_map[canon_name] = lat
        self._longitude_map[canon_name] = lon

    def get_home_club(self, canon_name: str) -> Optional[str]:
        """Return primary home club/tenant for a canonical venue name."""
        return self._home_club_map.get(canon_name)

    def get_home_teams(self, canon_name: str) -> List[str]:
        """Return home teams/squads using this ground."""
        return self._home_teams_map.get(canon_name, [])

    def get_competition(self, canon_name: str) -> Optional[str]:
        """Return competition (e.g. BHRDCA, ECA) for a canonical venue name."""
        return self._competition_map.get(canon_name)

    def set_tenant_info(
        self,
        canon_name: str,
        home_club: Optional[str] = None,
        home_teams: Optional[List[str]] = None,
        competition: Optional[str] = None,
    ) -> None:
        """Update or register tenant information for a canonical venue."""
        if home_club:
            self._home_club_map[canon_name] = home_club
        if home_teams:
            self._home_teams_map[canon_name] = home_teams
        if competition:
            self._competition_map[canon_name] = competition

    def _init_alias_map(self) -> None:
        """Build normalized lookup map of clean alias -> canonical name."""
        for v in self.venues:
            canon = v.name
            clean_canon = clean_string(canon)
            self._alias_map[clean_canon] = canon

            # Map all configured aliases
            for alias in v.aliases:
                clean_alias = clean_string(alias)
                if clean_alias:
                    self._alias_map[clean_alias] = canon

            # Also auto-generate common variations (e.g. "Kalang Park 1" -> "Kalang Park Oval 1")
            # If name has digit at end: "Kalang Park 1" -> "Kalang Park Oval 1", "Kalang Park Ground 1"
            match = re.match(r"^(.*?)\s+(\d+)$", canon.strip())
            if match:
                prefix = match.group(1)
                num = match.group(2)
                variations = [
                    f"{prefix} oval {num}",
                    f"{prefix} ground {num}",
                    f"{prefix} pitch {num}",
                    f"{prefix} #{num}",
                    f"{prefix} {num}",
                ]
                for var in variations:
                    clean_var = clean_string(var)
                    if clean_var not in self._alias_map:
                        self._alias_map[clean_var] = canon

    @property
    def canonical_names(self) -> List[str]:
        """Return list of all canonical venue names in order."""
        return self._canonical_names

    def resolve_venue(self, venue_name: Optional[str], surface_name: Optional[str] = None) -> Optional[str]:
        """Resolve a raw venue name and optional surface name to a canonical venue name.

        Tries:
        1. Full combined string: "{venue_name} {surface_name}"
        2. Surface alone if it contains venue information
        3. Venue alone
        4. Substring and keyword fuzzy match against catalog aliases
        """
        if not venue_name and not surface_name:
            return None

        v_raw = (venue_name or "").strip()
        s_raw = (surface_name or "").strip()

        # Candidates in order of specificity
        candidates = []
        if v_raw and s_raw:
            # If surface already contains venue name, avoid "Surrey Park Surrey Park Oval 1"
            if clean_string(v_raw) in clean_string(s_raw):
                candidates.append(s_raw)
            else:
                candidates.append(f"{v_raw} {s_raw}")
        if v_raw:
            candidates.append(v_raw)
        if s_raw:
            candidates.append(s_raw)

        # 1. Exact clean match
        for cand in candidates:
            cleaned = clean_string(cand)
            if cleaned in self._alias_map:
                return self._alias_map[cleaned]

        # 2. Numbered oval heuristic: e.g. "Kalang Park" with surface "Oval 1"
        if v_raw:
            cleaned_v = clean_string(v_raw)
            num_match = re.search(r"\b(\d+)\b", s_raw or "")
            if num_match:
                num = num_match.group(1)
                heuristics = [
                    f"{cleaned_v} {num}",
                    f"{cleaned_v} oval {num}",
                ]
                for h in heuristics:
                    if h in self._alias_map:
                        return self._alias_map[h]

        # 3. Substring match if full venue contains alias key
        for cand in candidates:
            cleaned = clean_string(cand)
            for alias_key, canon in self._alias_map.items():
                if len(alias_key) > 5 and alias_key in cleaned:
                    return canon

        return None


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate application configuration from a YAML file."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    try:
        config = AppConfig(**data)
        logger.debug("Loaded configuration from %s (%d venues configured)", path, len(config.venues))
        return config
    except Exception as e:
        logger.error("Failed to parse configuration from %s: %s", path, e)
        raise
