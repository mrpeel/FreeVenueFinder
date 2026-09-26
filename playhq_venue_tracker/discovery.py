"""Discovery engine for finding PlayHQ cricket grounds within a radius of a GPS coordinate."""

from __future__ import annotations

import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

import requests
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
import yaml

from playhq_venue_tracker.engine import infer_competition, parse_club_and_squad

console = Console()
logger = logging.getLogger(__name__)

# Default Origin: Laburnum Cricket Club (Kalang Park, Blackburn)
DEFAULT_LAT = -37.82774221477076
DEFAULT_LON = 145.14233395005854
DEFAULT_RADIUS_KM = 10.0

GRAPHQL_ENDPOINT = "https://api.playhq.com/graphql"

# Target local competitions around Laburnum CC (BHRDCA & ECA)
DEFAULT_SEASONS = [
    ("BHRDCA Seniors", "77fbfe27"),
    ("BHRDCA Juniors", "383bad37"),
    ("ECA Seniors", "56bebbf4"),
    ("ECA Juniors", "90325c78"),
]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance in kilometers between two lat/lon coordinates."""
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class PlayHQVenueFinder:
    """Discovers all grounds used in local PlayHQ competitions within a given geographic radius."""

    def __init__(
        self,
        center_lat: float = DEFAULT_LAT,
        center_lon: float = DEFAULT_LON,
        radius_km: float = DEFAULT_RADIUS_KM,
        tenant: str = "cricket-australia",
    ):
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_km = radius_km
        self.tenant = tenant
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.playhq.com/cricket-australia",
            "Origin": "https://www.playhq.com",
            "Content-Type": "application/json",
            "tenant": self.tenant,
            "request-id": str(uuid.uuid4()),
        })

    def _query_graphql(self, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a GraphQL query to PlayHQ API."""
        try:
            resp = self.session.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def get_season_grades(self, season_id: str) -> List[Tuple[str, str]]:
        """Get all grades in a season. Returns list of (grade_name, grade_id)."""
        query = """
        query getGrades($id: String!) {
          discoverSeason(seasonID: $id) {
            name
            grades {
              id
              name
              day { name }
            }
          }
        }
        """
        data = self._query_graphql(query, {"id": season_id})
        if not data:
            return []
        grades = data.get("data", {}).get("discoverSeason", {}).get("grades", []) or []
        return [(g.get("name", ""), g.get("id", "")) for g in grades if g.get("id")]

    def extract_venues_from_grade(self, grade_id: str, comp_name: str = "") -> List[Dict[str, Any]]:
        """Extract all venues, surfaces, and home teams scheduled in a grade."""
        query = """
        query gradeAllRounds($gradeID: ID!) {
          discoverGradeFixture(gradeID: $gradeID) {
            id
            name
            games {
              home {
                ... on DiscoverTeam { id name }
                ... on ProvisionalTeam { name }
              }
              allocation {
                court {
                  name
                  venue {
                    name
                    address
                    suburb
                    latitude
                    longitude
                  }
                }
              }
            }
          }
        }
        """
        data = self._query_graphql(query, {"gradeID": grade_id})
        if not data:
            return []

        rounds = data.get("data", {}).get("discoverGradeFixture", []) or []
        venues = []
        for r in rounds:
            grade_name = r.get("name") or ""
            for g in r.get("games", []) or []:
                alloc = g.get("allocation") or {}
                court = alloc.get("court") or {}
                venue = court.get("venue") or {}

                v_name = venue.get("name")
                c_name = court.get("name")
                lat = venue.get("latitude")
                lon = venue.get("longitude")

                home_node = g.get("home") or {}
                home_team = home_node.get("name") if isinstance(home_node, dict) else None

                if v_name and lat and lon:
                    try:
                        v_lat = float(lat)
                        v_lon = float(lon)
                        venues.append({
                            "venue_name": v_name.strip(),
                            "surface_name": c_name.strip() if c_name else None,
                            "address": venue.get("address"),
                            "suburb": venue.get("suburb"),
                            "lat": v_lat,
                            "lon": v_lon,
                            "home_team": home_team,
                            "grade_name": grade_name,
                            "comp_name": comp_name,
                        })
                    except (ValueError, TypeError):
                        pass
        return venues

    def discover_nearby_venues(
        self,
        seasons: Optional[List[Tuple[str, str]]] = None,
        max_grades_per_season: int = 7,
    ) -> List[Dict[str, Any]]:
        """Search across target seasons for venues within radius."""
        seasons = seasons or DEFAULT_SEASONS
        raw_venues: Dict[str, Dict[str, Any]] = {}

        console.print(
            f"Scanning PlayHQ competitions around coordinate "
            f"([bold cyan]{self.center_lat:.4f}, {self.center_lon:.4f}[/bold cyan]) "
            f"within [bold green]{self.radius_km:.1f} km[/bold green]..."
        )

        total_grades_to_scan = 0
        season_grade_map = []
        for comp_name, season_id in seasons:
            grades = self.get_season_grades(season_id)
            sample_grades = grades[:max_grades_per_season]
            season_grade_map.append((comp_name, sample_grades))
            total_grades_to_scan += len(sample_grades)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Querying fixtures for venues...", total=total_grades_to_scan)

            for comp_name, sample_grades in season_grade_map:
                for grade_name, grade_id in sample_grades:
                    progress.update(task, description=f"{comp_name}: {grade_name[:30]}")
                    venues = self.extract_venues_from_grade(grade_id)
                    for item in venues:
                        dist = haversine(self.center_lat, self.center_lon, item["lat"], item["lon"])
                        if dist <= self.radius_km:
                            v_key = item["venue_name"]
                            if v_key not in raw_venues:
                                raw_venues[v_key] = {
                                    "name": v_key,
                                    "suburb": item["suburb"],
                                    "address": item["address"],
                                    "lat": item["lat"],
                                    "lon": item["lon"],
                                    "distance_km": round(dist, 2),
                                    "surfaces": set(),
                                    "surface_tenants": {},
                                    "venue_tenants": {"clubs": {}, "teams": {}, "comps": {}},
                                }
                            s_name = item.get("surface_name")
                            if s_name:
                                raw_venues[v_key]["surfaces"].add(s_name)
                                if s_name not in raw_venues[v_key]["surface_tenants"]:
                                    raw_venues[v_key]["surface_tenants"][s_name] = {"clubs": {}, "teams": {}, "comps": {}}

                            if item.get("home_team"):
                                club, squad = parse_club_and_squad(item["home_team"])
                                comp = infer_competition(item.get("grade_name") or "") or item.get("comp_name") or "Cricket"

                                vt = raw_venues[v_key]["venue_tenants"]
                                vt["clubs"][club] = vt["clubs"].get(club, 0) + 1
                                vt["teams"][squad or club] = vt["teams"].get(squad or club, 0) + 1
                                vt["comps"][comp] = vt["comps"].get(comp, 0) + 1

                                if s_name:
                                    st = raw_venues[v_key]["surface_tenants"][s_name]
                                    st["clubs"][club] = st["clubs"].get(club, 0) + 1
                                    st["teams"][squad or club] = st["teams"].get(squad or club, 0) + 1
                                    st["comps"][comp] = st["comps"].get(comp, 0) + 1

                    progress.advance(task)

        # Format sorted list
        results = []
        for v in sorted(raw_venues.values(), key=lambda x: x["distance_km"]):
            v["surfaces"] = sorted(list(v["surfaces"]))
            vt = v["venue_tenants"]
            v["home_club"] = max(vt["clubs"].items(), key=lambda x: x[1])[0] if vt["clubs"] else None
            v["home_teams"] = [sq for sq, _ in sorted(vt["teams"].items(), key=lambda x: x[1], reverse=True)[:3]] if vt["teams"] else []
            v["competition"] = max(vt["comps"].items(), key=lambda x: x[1])[0] if vt["comps"] else None
            results.append(v)

        return results


def _resolve_tenant(
    tenant_dict: Dict[str, Any],
    fallback_club: Optional[str] = None,
    fallback_teams: Optional[List[str]] = None,
    fallback_comp: Optional[str] = None,
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """Determine top club, squads, and competition from a tenant tracking dictionary."""
    clubs = tenant_dict.get("clubs", {})
    if clubs:
        top_club = max(clubs.items(), key=lambda x: x[1])[0]
        top_teams = [sq for sq, _ in sorted(tenant_dict.get("teams", {}).items(), key=lambda x: x[1], reverse=True)[:3]]
        comps = tenant_dict.get("comps", {})
        top_comp = max(comps.items(), key=lambda x: x[1])[0] if comps else fallback_comp
        return top_club, top_teams, top_comp
    return fallback_club, fallback_teams or [], fallback_comp


def generate_venue_configs(discovered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert discovered venues and their surfaces into structured VenueConfig items with aliases and home tenant info."""
    configs: List[Dict[str, Any]] = []

    for v in discovered:
        venue_name = v["name"]
        surfaces = v["surfaces"]
        v_club = v.get("home_club")
        v_teams = v.get("home_teams") or []
        v_comp = v.get("competition")
        surf_tenants = v.get("surface_tenants") or {}

        if surfaces and len(surfaces) > 1:
            for s in surfaces:
                canon_name, aliases = _build_surface_aliases(venue_name, s)
                st = surf_tenants.get(s, {})
                s_club, s_teams, s_comp = _resolve_tenant(st, v_club, v_teams, v_comp)
                configs.append({
                    "name": canon_name,
                    "suburb": v.get("suburb"),
                    "distance_km": v.get("distance_km"),
                    "latitude": v.get("lat"),
                    "longitude": v.get("lon"),
                    "home_club": s_club,
                    "home_teams": s_teams,
                    "competition": s_comp,
                    "aliases": aliases,
                })
        elif surfaces and len(surfaces) == 1:
            s = surfaces[0]
            canon_name, aliases = _build_surface_aliases(venue_name, s)
            st = surf_tenants.get(s, {})
            s_club, s_teams, s_comp = _resolve_tenant(st, v_club, v_teams, v_comp)
            configs.append({
                "name": canon_name,
                "suburb": v.get("suburb"),
                "distance_km": v.get("distance_km"),
                "latitude": v.get("lat"),
                "longitude": v.get("lon"),
                "home_club": s_club,
                "home_teams": s_teams,
                "competition": s_comp,
                "aliases": aliases,
            })
        else:
            aliases = [
                f"{venue_name} Oval 1",
                f"{venue_name} #1",
                f"{venue_name} 1",
            ]
            configs.append({
                "name": venue_name,
                "suburb": v.get("suburb"),
                "distance_km": v.get("distance_km"),
                "latitude": v.get("lat"),
                "longitude": v.get("lon"),
                "home_club": v_club,
                "home_teams": v_teams,
                "competition": v_comp,
                "aliases": aliases,
            })

    return configs


def _build_surface_aliases(venue_name: str, surface_name: str) -> Tuple[str, List[str]]:
    """Create a clean canonical name and realistic aliases from a venue name and surface name."""
    clean_s = re.sub(r"[#\-–]", " ", surface_name).strip()
    num_match = re.search(r"\b(\d+)\b", clean_s)

    aliases: Set[str] = set()
    aliases.add(surface_name)
    aliases.add(f"{venue_name} {surface_name}")
    aliases.add(f"{venue_name} - {surface_name}")

    if num_match:
        num = num_match.group(1)
        canon = f"{venue_name} {num}"
        aliases.add(f"{venue_name} Oval {num}")
        aliases.add(f"{venue_name} #{num}")
        aliases.add(f"{venue_name} {num}")
        aliases.add(f"{venue_name} Ground {num}")
        aliases.add(f"{venue_name} (Oval {num})")
    elif any(d in clean_s.lower() for d in ("north", "south", "east", "west")):
        for d in ("South East", "South West", "North East", "North West", "North", "South", "East", "West"):
            if d.lower() in clean_s.lower():
                canon = f"{venue_name} {d}"
                aliases.add(f"{venue_name} Oval {d}")
                aliases.add(f"{venue_name} ({d})")
                aliases.add(f"{venue_name} - {d}")
                aliases.add(f"{venue_name} {d} Oval")
                break
        else:
            canon = f"{venue_name} ({surface_name})"
    else:
        if surface_name.lower() == venue_name.lower():
            canon = venue_name
            aliases.add(f"{venue_name} Oval 1")
            aliases.add(f"{venue_name} 1")
        else:
            canon = f"{venue_name} - {surface_name}"
            aliases.add(f"{venue_name} ({surface_name})")

    return canon, sorted(list(aliases))


def update_config_yaml(
    config_path: Path,
    venue_configs: List[Dict[str, Any]],
    radius_km: float,
    center_coords: Tuple[float, float],
) -> None:
    """Update config.yaml with discovered venues while preserving date range and slot settings."""
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    clean_venues_list = []
    for v in venue_configs:
        entry = {
            "name": v["name"],
            "aliases": v["aliases"],
        }
        if "distance_km" in v and v["distance_km"] is not None:
            entry["distance_km"] = v["distance_km"]
        if "suburb" in v and v["suburb"] is not None:
            entry["suburb"] = v["suburb"]
        if "latitude" in v and v["latitude"] is not None:
            entry["latitude"] = v["latitude"]
        if "longitude" in v and v["longitude"] is not None:
            entry["longitude"] = v["longitude"]
        if "home_club" in v and v["home_club"]:
            entry["home_club"] = v["home_club"]
        if "home_teams" in v and v["home_teams"]:
            entry["home_teams"] = v["home_teams"]
        if "competition" in v and v["competition"]:
            entry["competition"] = v["competition"]
        clean_venues_list.append(entry)

    cfg["venues"] = clean_venues_list

    if "association_url" not in cfg or not cfg["association_url"]:
        cfg["association_url"] = "https://www.playhq.com/cricket-australia/org/eastern-cricket-association/summer-202627"
    if "date_range" not in cfg:
        cfg["date_range"] = {"start_date": "2026-10-03", "end_date": "2027-02-27"}
    if "target_day" not in cfg:
        cfg["target_day"] = "Saturday"
    if "time_slots" not in cfg:
        cfg["time_slots"] = {
            "Morning": {"cutoff": "12:00", "description": "Start times before 12:00 PM (e.g., Juniors)"},
            "Afternoon": {"cutoff": "12:00", "description": "Start times 12:00 PM and later (e.g., Seniors)"},
        }

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("# PlayHQ Venue Tracker Configuration\n")
        f.write(f"# Populated with grounds within {radius_km}km of Laburnum CC {center_coords}\n\n")
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False, allow_unicode=True)

    console.print(f"\n[bold green]✓ Successfully updated {config_path} with {len(clean_venues_list)} grounds![/bold green]")
