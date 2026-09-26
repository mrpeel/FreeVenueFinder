"""PlayHQ scraper and parser for cricket fixtures."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
import random
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse
import uuid

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from playhq_venue_tracker.models import MatchRecord, parse_time_slot

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

GRAPHQL_ENDPOINT = "https://api.playhq.com/graphql"


class PlayHQScraper:
    """Robust scraper for PlayHQ cricket fixtures using session pooling and __NEXT_DATA__ extraction."""

    def __init__(
        self,
        min_sleep: float = 0.5,
        max_sleep: float = 1.0,
        headers: Optional[Dict[str, str]] = None,
        tenant: str = "cricket-australia",
    ):
        self.min_sleep = min_sleep
        self.max_sleep = max_sleep
        self.tenant = tenant
        self.session = self._create_session(headers or DEFAULT_HEADERS)
        self.session.headers.update({
            "tenant": self.tenant,
            "request-id": str(uuid.uuid4()),
        })

    def _create_session(self, headers: Dict[str, str]) -> requests.Session:
        """Create a requests session with retries on 429 and 5xx."""
        session = requests.Session()
        session.headers.update(headers)

        retries = Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _polite_delay(self) -> None:
        """Sleep politely between network requests."""
        if self.max_sleep > 0:
            delay = random.uniform(self.min_sleep, self.max_sleep)
            time.sleep(delay)

    def fetch_page_html(self, url: str) -> Optional[str]:
        """Fetch page HTML with error handling and polite rate-limiting."""
        self._polite_delay()
        try:
            logger.info("Fetching fixture page: %s", url)
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.text
            logger.warning("HTTP %d when fetching %s", resp.status_code, url)
            return None
        except Exception as e:
            logger.error("Network error fetching %s: %s", url, e)
            return None

    def extract_next_data_json(self, html: str) -> Optional[Dict[str, Any]]:
        """Extract JSON payload from <script id="__NEXT_DATA__" type="application/json">."""
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            try:
                data = json.loads(script.string)
                logger.debug("Successfully extracted __NEXT_DATA__ JSON from HTML")
                return data
            except json.JSONDecodeError as e:
                logger.warning("Failed to decode JSON inside __NEXT_DATA__: %s", e)
                return None

        # Fallback: look for script tags that contain Next.js props or pageProps
        for s in soup.find_all("script"):
            content = s.string or ""
            if "__NEXT_DATA__" in content or "pageProps" in content:
                match = re.search(r"(\{.*\"pageProps\".*\})", content, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group(1))
                    except Exception:
                        pass
        return None

    def extract_matches_from_html(self, html: str, source_url: Optional[str] = None) -> List[MatchRecord]:
        """Extract MatchRecord objects from page HTML containing Next.js hydration payload."""
        data = self.extract_next_data_json(html)
        if not data:
            logger.debug("No __NEXT_DATA__ payload located in page HTML")
            return []
        return self.extract_matches_from_json(data, source_url=source_url)

    def extract_matches_from_json(
        self,
        data: Dict[str, Any],
        source_url: Optional[str] = None,
        default_grade_name: Optional[str] = None,
    ) -> List[MatchRecord]:
        """Recursively navigate JSON object (props.pageProps) to defensively extract match records."""
        matches: List[MatchRecord] = []
        seen_match_ids: Set[str] = set()

        # If full Next.js structure, focus on props.pageProps
        props = data.get("props", {}).get("pageProps", data)

        def visitor(node: Any, current_grade: Optional[str] = None, current_round: Optional[str] = None) -> None:
            if isinstance(node, dict):
                # Update grade hint if present on parent node (e.g. round or grade container)
                grade_hint = current_grade or default_grade_name
                round_hint = current_round

                if "grade" in node and isinstance(node["grade"], dict) and "name" in node["grade"]:
                    grade_hint = node["grade"]["name"]
                elif "gradeName" in node and isinstance(node["gradeName"], str):
                    grade_hint = node["gradeName"]
                elif "name" in node and isinstance(node["name"], str):
                    n_str = node["name"].strip()
                    if any(k in n_str.lower() for k in ("grade", "senior", "junior", "shield", "division")):
                        grade_hint = n_str
                    elif any(k in n_str.lower() for k in ("round", "semi", "final", "quarter", "prelim")):
                        round_hint = n_str

                # Check if this dictionary represents a match/game record
                if self._is_game_node(node):
                    try:
                        record = self._parse_single_game(
                            node,
                            grade_hint=grade_hint,
                            round_hint=round_hint,
                            source_url=source_url,
                        )
                        if record and record.match_id not in seen_match_ids:
                            matches.append(record)
                            seen_match_ids.add(record.match_id)
                    except Exception as err:
                        logger.warning("Error parsing match record: %s. Node data: %s", err, node)

                # Recursively explore nested values
                for v in node.values():
                    visitor(v, grade_hint, round_hint)

            elif isinstance(node, list):
                for item in node:
                    visitor(item, current_grade, current_round)

        visitor(props, default_grade_name, None)
        logger.info("Extracted %d unique matches from JSON payload", len(matches))
        return matches

    def _is_game_node(self, node: Dict[str, Any]) -> bool:
        """Heuristically identify whether a dictionary represents a PlayHQ match/game."""
        # Must have an ID of some kind
        has_id = any(k in node for k in ("id", "gameId", "match_id", "alias"))
        if not has_id:
            return False

        # Must have team concepts
        has_teams = ("home" in node and "away" in node) or ("homeTeam" in node and "awayTeam" in node) or ("teams" in node)

        # Must have date or venue concept
        has_date_or_venue = any(
            k in node for k in ("date", "dates", "gameDate", "allocation", "venue", "court", "ground")
        )

        return bool(has_teams and has_date_or_venue)

    def _parse_single_game(
        self,
        node: Dict[str, Any],
        grade_hint: Optional[str] = None,
        round_hint: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Optional[MatchRecord]:
        """Defensively parse a single match dictionary into a validated MatchRecord."""
        match_id = str(node.get("id") or node.get("gameId") or node.get("match_id") or node.get("alias") or "")
        if not match_id:
            logger.warning("Skipping match node without valid ID")
            return None

        # Extract Teams
        home_team = self._extract_team_name(node.get("home") or node.get("homeTeam"))
        away_team = self._extract_team_name(node.get("away") or node.get("awayTeam"))
        if not home_team or not away_team:
            teams = node.get("teams")
            if isinstance(teams, list) and len(teams) >= 2:
                home_team = home_team or self._extract_team_name(teams[0])
                away_team = away_team or self._extract_team_name(teams[1])

        home_team = home_team or "TBD Home Team"
        away_team = away_team or "TBD Away Team"

        # Extract Date and Start Time
        match_date, start_time = self._extract_date_and_time(node)
        if not match_date:
            logger.debug("Match %s omitted: No valid date found", match_id)
            return None

        # Extract Venue and Surface
        venue_name, surface_name = self._extract_venue_and_surface(node)
        if not venue_name:
            logger.debug("Match %s omitted: No venue information", match_id)
            return None

        # Extract Grade
        grade_name = grade_hint
        if "grade" in node and isinstance(node["grade"], dict):
            grade_name = node["grade"].get("name") or grade_name
        elif "gradeName" in node and isinstance(node["gradeName"], str):
            grade_name = node["gradeName"]
        grade_name = grade_name or "Cricket Grade"

        # Round & Status
        round_name = round_hint
        if "round" in node and isinstance(node["round"], dict):
            round_name = node["round"].get("name") or round_name
        elif "roundName" in node and isinstance(node["roundName"], str):
            round_name = node["roundName"]

        status = node.get("status")
        if isinstance(status, dict):
            status = status.get("name") or status.get("value")

        return MatchRecord(
            match_id=match_id,
            date=match_date,
            start_time=start_time,
            time_slot="Unknown",  # Auto-populated by model validator
            venue_name=venue_name,
            surface_name=surface_name,
            grade_name=grade_name,
            home_team=home_team,
            away_team=away_team,
            round_name=round_name,
            status=str(status) if status else None,
            source_url=source_url,
        )

    def _extract_team_name(self, team_obj: Any) -> Optional[str]:
        """Safely extract team name from string, dict, or nested structure."""
        if not team_obj:
            return None
        if isinstance(team_obj, str):
            return team_obj.strip()
        if isinstance(team_obj, dict):
            name = team_obj.get("name")
            if name:
                return str(name).strip()
            # If nested team object
            if "team" in team_obj and isinstance(team_obj["team"], dict):
                return self._extract_team_name(team_obj["team"])
        return None

    def _extract_date_and_time(self, node: Dict[str, Any]) -> Tuple[Optional[datetime.date], Optional[str]]:
        """Extract date and time strings defensively from match node."""
        date_str = None
        time_str = None

        # Check allocation first (PlayHQ standard structure)
        allocation = node.get("allocation")
        if isinstance(allocation, dict):
            time_str = allocation.get("time") or time_str
            dt_list = allocation.get("dateTimeList")
            if isinstance(dt_list, list) and dt_list:
                first_dt = dt_list[0]
                if isinstance(first_dt, dict):
                    date_str = first_dt.get("date") or date_str
                    time_str = first_dt.get("time") or time_str

        # Check direct fields
        if not date_str:
            date_str = node.get("date") or node.get("gameDate")
            if not date_str:
                dates = node.get("dates")
                if isinstance(dates, list) and dates:
                    date_str = dates[0]

        if not time_str and "time" in node:
            time_str = node["time"]

        # Parse date_str
        parsed_date: Optional[datetime.date] = None
        if date_str:
            try:
                # Handle ISO e.g. 2026-10-03T09:00:00Z
                if "T" in str(date_str):
                    parts = str(date_str).split("T")
                    parsed_date = datetime.date.fromisoformat(parts[0].strip())
                    if not time_str and len(parts) > 1:
                        # Extract HH:MM
                        time_match = re.match(r"^(\d{2}:\d{2})", parts[1])
                        if time_match:
                            time_str = time_match.group(1)
                else:
                    parsed_date = datetime.date.fromisoformat(str(date_str).strip())
            except Exception:
                logger.warning("Could not parse date string: %s", date_str)

        return parsed_date, time_str

    def _extract_venue_and_surface(self, node: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Extract venue name and surface/court/oval name from PlayHQ match node."""
        venue_name: Optional[str] = None
        surface_name: Optional[str] = None

        # PlayHQ allocation format: allocation.court.venue
        allocation = node.get("allocation")
        if isinstance(allocation, dict):
            court = allocation.get("court")
            if isinstance(court, dict):
                surface_name = court.get("name")
                venue_obj = court.get("venue")
                if isinstance(venue_obj, dict):
                    venue_name = venue_obj.get("name")
                elif isinstance(venue_obj, str):
                    venue_name = venue_obj

        # Direct venue object format
        if not venue_name and "venue" in node:
            v_val = node["venue"]
            if isinstance(v_val, dict):
                venue_name = v_val.get("name")
            elif isinstance(v_val, str):
                venue_name = v_val

        # Direct court / ground / surface format
        if not surface_name:
            for key in ("surface", "court", "ground", "oval", "surface_name"):
                if key in node:
                    val = node[key]
                    if isinstance(val, dict):
                        surface_name = val.get("name")
                    elif isinstance(val, str):
                        surface_name = val
                    if surface_name:
                        break

        # Direct venue_name field
        if not venue_name and "venue_name" in node and isinstance(node["venue_name"], str):
            venue_name = node["venue_name"]

        # If venue is missing but surface has full name (e.g. "Kalang Park Oval 1")
        if not venue_name and surface_name:
            venue_name = surface_name
            surface_name = None

        return (venue_name.strip() if venue_name else None, surface_name.strip() if surface_name else None)

    def discover_grade_links(self, base_url: str, html: Optional[str] = None) -> List[str]:
        """Crawl an association directory page to find all Saturday fixture URLs."""
        if not html:
            html = self.fetch_page_html(base_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        discovered_urls: Set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            # Normalize relative links
            full_url = urllib.parse.urljoin(base_url, href)

            # Look for fixture-like paths
            if any(p in href for p in ("/fixture", "/fixtures-and-ladders", "/grade/")):
                discovered_urls.add(full_url)

        logger.info("Discovered %d grade fixture URLs from %s", len(discovered_urls), base_url)
        return sorted(list(discovered_urls))

    def get_season_grades(self, season_id: str) -> List[Tuple[str, str]]:
        """Get all grades in a season via GraphQL. Returns list of (grade_name, grade_id)."""
        query = """
        query getSeasonGrades($id: String!) {
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
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "tenant": self.tenant,
            "Referer": "https://www.playhq.com/cricket-australia",
            "Origin": "https://www.playhq.com",
            "request-id": str(uuid.uuid4()),
        }
        self._polite_delay()
        try:
            logger.info("Querying PlayHQ GraphQL for season %s", season_id)
            resp = self.session.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": {"id": season_id}},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                grades = data.get("data", {}).get("discoverSeason", {}).get("grades", []) or []
                return [(g.get("name", ""), g.get("id", "")) for g in grades if g.get("id")]
            logger.warning("GraphQL season request failed with status %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("GraphQL error querying season %s: %s", season_id, e)
        return []

    def fetch_grade_fixtures_graphql(
        self, grade_id: str, grade_name: Optional[str] = None
    ) -> List[MatchRecord]:
        """Fetch all fixtures for a grade directly from PlayHQ's GraphQL API (discoverGradeFixture)."""
        query = """
        query gradeAllRounds($gradeID: ID!) {
          discoverGradeFixture(gradeID: $gradeID) {
            id
            name
            games {
              id
              date
              dates
              home {
                ... on DiscoverTeam { id name }
                ... on ProvisionalTeam { name }
              }
              away {
                ... on DiscoverTeam { id name }
                ... on ProvisionalTeam { name }
              }
              allocation {
                time
                court {
                  id
                  name
                  venue {
                    id
                    name
                  }
                }
              }
              status { name value }
            }
          }
        }
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "tenant": self.tenant,
            "Referer": "https://www.playhq.com/cricket-australia",
            "Origin": "https://www.playhq.com",
            "request-id": str(uuid.uuid4()),
        }
        self._polite_delay()
        try:
            logger.info("Querying PlayHQ GraphQL for grade %s (%s)", grade_id, grade_name or "")
            resp = self.session.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": {"gradeID": grade_id}},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                payload = resp.json()
                return self.extract_matches_from_json(
                    payload,
                    source_url=f"graphql:{grade_id}",
                    default_grade_name=grade_name,
                )
            logger.warning("GraphQL request failed with status %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("GraphQL error querying grade %s: %s", grade_id, e)

        return []

    def fetch_season_fixtures_graphql(
        self,
        season_id: str,
        season_name: Optional[str] = None,
        progress_callback: Optional[Any] = None,
    ) -> List[MatchRecord]:
        """Fetch all matches across all grades in a season via GraphQL."""
        grades = self.get_season_grades(season_id)
        logger.info("Found %d grades for season %s (%s)", len(grades), season_id, season_name or "")
        all_matches: List[MatchRecord] = []
        seen_ids: Set[str] = set()

        for g_name, g_id in grades:
            if progress_callback:
                progress_callback(season_name or season_id, g_name)
            g_matches = self.fetch_grade_fixtures_graphql(g_id, grade_name=g_name)
            for m in g_matches:
                if m.match_id not in seen_ids:
                    all_matches.append(m)
                    seen_ids.add(m.match_id)

        return all_matches


def save_fixtures_to_cache(matches: List[MatchRecord], cache_file: str | Path = "data/fixtures_cache.json") -> Path:
    """Save extracted match records to a JSON cache file."""
    path = Path(cache_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "metadata": {
            "cached_at": datetime.datetime.now().isoformat(),
            "total_matches": len(matches),
        },
        "matches": [m.model_dump(mode="json") for m in matches],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info("Saved %d matches to cache: %s", len(matches), path)
    return path


def load_fixtures_from_cache(cache_file: str | Path = "data/fixtures_cache.json") -> List[MatchRecord]:
    """Load and validate match records from a JSON cache file."""
    path = Path(cache_file)
    if not path.is_file():
        raise FileNotFoundError(f"Fixtures cache file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_matches = data.get("matches", [])
    matches = [MatchRecord(**item) for item in raw_matches]
    logger.info("Loaded %d matches from cache: %s", len(matches), path)
    return matches
