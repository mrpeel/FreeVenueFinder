"""Command-line interface for PlayHQ Venue Tracker."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
import typer

from playhq_venue_tracker.config import load_config
from playhq_venue_tracker.engine import AvailabilityEngine
from playhq_venue_tracker.exporters import export_csv, export_excel, print_rich_summary
from playhq_venue_tracker.mock_data import build_mock_html_page, generate_mock_fixtures_dataset
from playhq_venue_tracker.models import MatchRecord
from playhq_venue_tracker.scraper import (
    PlayHQScraper,
    load_fixtures_from_cache,
    save_fixtures_to_cache,
)

app = typer.Typer(
    name="playhq-venue-tracker",
    help="Audit PlayHQ cricket fixtures and track ground availability across an association.",
    add_completion=False,
)

console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure structured console logging using RichHandler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
        force=True,
    )


@app.command()
def fetch(
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml"
    ),
    cache_file: Path = typer.Option(
        Path("data/fixtures_cache.json"), "--output", "-o", help="Path to save raw fixtures cache"
    ),
    url: Optional[str] = typer.Option(
        None, "--url", "-u", help="Override association or fixture URL"
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use realistic offline mock data instead of live scraping"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
) -> None:
    """Scrape PlayHQ cricket fixtures and cache them locally."""
    setup_logging(verbose)
    console.print("[bold blue]Starting PlayHQ Fixtures Extraction...[/bold blue]")

    config = load_config(config_path)
    all_matches: list[MatchRecord] = []

    if mock:
        console.print("[yellow]Mock mode active: Generating realistic synthetic season fixtures...[/yellow]")
        mock_html = build_mock_html_page()
        scraper = PlayHQScraper()
        all_matches = scraper.extract_matches_from_html(mock_html, source_url="mock:fixtures")
    else:
        scraper = PlayHQScraper(min_sleep=0.2, max_sleep=0.4)
        seen_ids: set[str] = set()

        # 1. Fetch seasons via GraphQL API if configured
        if config.seasons:
            console.print(
                f"Fetching fixtures for [bold green]{len(config.seasons)}[/bold green] configured season(s)..."
            )
            season_grades = []
            for s in config.seasons:
                grades = scraper.get_season_grades(s.id)
                console.print(f"  • {s.name} ({s.id}): [cyan]{len(grades)}[/cyan] grades found")
                for g_name, g_id in grades:
                    season_grades.append((s.name, g_name, g_id))

            if season_grades:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Fetching fixtures via PlayHQ API...", total=len(season_grades))
                    for s_name, g_name, g_id in season_grades:
                        progress.update(task, description=f"{s_name[:15]}: {g_name[:30]}")
                        g_matches = scraper.fetch_grade_fixtures_graphql(g_id, grade_name=g_name)
                        for m in g_matches:
                            if m.match_id not in seen_ids:
                                all_matches.append(m)
                                seen_ids.add(m.match_id)
                        progress.advance(task)

        # 2. Fetch any explicit fixture URLs or HTML crawling fallback
        target_url = url or (config.association_url if not config.seasons else None)
        urls_to_scrape = list(config.fixture_urls)

        if not all_matches and not urls_to_scrape and target_url:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task(description=f"Crawling directory at {target_url}...", total=None)
                discovered = scraper.discover_grade_links(target_url)
                if discovered:
                    console.print(f"Discovered [bold green]{len(discovered)}[/bold green] grade fixture pages.")
                    urls_to_scrape.extend(discovered)
                else:
                    urls_to_scrape.append(target_url)

        if urls_to_scrape:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Fetching HTML fixtures...", total=len(urls_to_scrape))
                for page_url in urls_to_scrape:
                    progress.update(task, description=f"Scraping {page_url[:60]}...")
                    html = scraper.fetch_page_html(page_url)
                    if html:
                        page_matches = scraper.extract_matches_from_html(html, source_url=page_url)
                        for m in page_matches:
                            if m.match_id not in seen_ids:
                                all_matches.append(m)
                                seen_ids.add(m.match_id)
                    progress.advance(task)

        if not all_matches:
            console.print("[bold red]No fixtures found. Check configured seasons or fixture URLs.[/bold red]")
            raise typer.Exit(code=1)

    # Save results to cache file
    saved_path = save_fixtures_to_cache(all_matches, cache_file)
    console.print(
        f"[bold green]Successfully cached {len(all_matches)} matches to {saved_path}[/bold green]"
    )


@app.command()
def analyze(
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml"
    ),
    cache_file: Path = typer.Option(
        Path("data/fixtures_cache.json"), "--cache", help="Path to cached fixtures JSON"
    ),
    start_date: Optional[str] = typer.Option(
        None, "--start-date", help="Override start date (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None, "--end-date", help="Override end date (YYYY-MM-DD)"
    ),
    export: str = typer.Option(
        "both", "--export", "-e", help="Export format: 'csv', 'xlsx', 'both', or 'none'"
    ),
    slot: str = typer.Option(
        "all", "--slot", "-s", help="Time slot to audit: 'afternoon', 'morning', or 'all' (default: 'all')"
    ),
    afternoon_only: bool = typer.Option(
        False, "--afternoon-only", help="Exclude morning matches and audit Afternoon slots only"
    ),
    limit: int = typer.Option(
        8, "--limit", "-l", help="Number of closest grounds to display per Saturday in terminal summary"
    ),
    target_date: Optional[str] = typer.Option(
        None, "--date", "-d", help="Inspect a specific date (YYYY-MM-DD) and list all free grounds in distance order"
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output-dir", help="Directory to save export files"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
) -> None:
    """Analyze cached fixtures, apply venue catalog filtering, and generate matrices."""
    setup_logging(verbose)
    config = load_config(config_path)

    # Clean parameter types (guarding against Typer OptionInfo when invoked directly in Python)
    clean_target_date = target_date if isinstance(target_date, str) else None
    clean_start_date = start_date if isinstance(start_date, str) else None
    clean_end_date = end_date if isinstance(end_date, str) else None
    clean_limit = limit if isinstance(limit, int) else 8

    # Override date range if provided
    if clean_target_date:
        s_date = datetime.date.fromisoformat(clean_target_date)
        e_date = datetime.date.fromisoformat(clean_target_date)
        clean_limit = 999  # Show all grounds for single date inspection
    else:
        s_date = datetime.date.fromisoformat(clean_start_date) if clean_start_date else config.date_range.start_date
        e_date = datetime.date.fromisoformat(clean_end_date) if clean_end_date else config.date_range.end_date

    # Determine active slots to audit
    active_slots = None
    if afternoon_only or slot.lower() in ("afternoon", "pm"):
        active_slots = ["Afternoon"]
    elif slot.lower() in ("morning", "am"):
        active_slots = ["Morning"]
    elif slot.lower() not in ("all", "both", ""):
        active_slots = [slot.strip().capitalize()]

    try:
        matches = load_fixtures_from_cache(cache_file)
    except FileNotFoundError:
        console.print(
            f"[bold red]Cache file not found at {cache_file}. Run 'fetch' first or provide '--mock'.[/bold red]"
        )
        raise typer.Exit(code=1)

    slots_display = ", ".join(active_slots) if active_slots else ", ".join(config.target_slots or ["Morning", "Afternoon"])
    console.print(
        f"Analyzing availability ({slots_display}) from [bold cyan]{s_date}[/bold cyan] to [bold cyan]{e_date}[/bold cyan] "
        f"across [bold]{len(config.venues)}[/bold] monitored grounds..."
    )

    engine = AvailabilityEngine(config)
    records = engine.process_fixtures(matches, start_date=s_date, end_date=e_date, active_slots=active_slots)

    # Render terminal table in distance order
    print_rich_summary(records, console=console, max_grounds_per_date=clean_limit)

    # Handle Exports
    output_dir.mkdir(parents=True, exist_ok=True)
    export_fmt = export.lower().strip()

    if export_fmt in ("csv", "both"):
        csv_path = output_dir / "venue_availability.csv"
        export_csv(records, csv_path)
        avail_path = output_dir / "available_grounds.csv"
        console.print(f"[bold green]Saved Full Audit CSV: [/bold green]{csv_path}")
        console.print(f"[bold green]Saved Available Grounds CSV (Distance Order): [/bold green]{avail_path}")

    if export_fmt in ("xlsx", "both"):
        xlsx_path = output_dir / "venue_availability.xlsx"
        export_excel(engine, records, matches, xlsx_path)
        console.print(f"[bold green]Saved Formatted Excel Matrix: [/bold green]{xlsx_path} (Sheet 1: 'Available Grounds')")


@app.command()
def run(
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml"
    ),
    start_date: Optional[str] = typer.Option(
        None, "--start-date", help="Start date (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None, "--end-date", help="End date (YYYY-MM-DD)"
    ),
    slot: str = typer.Option(
        "all", "--slot", "-s", help="Time slot to audit: 'afternoon', 'morning', or 'all' (default: 'all')"
    ),
    afternoon_only: bool = typer.Option(
        False, "--afternoon-only", help="Exclude morning matches and audit Afternoon slots only"
    ),
    export: str = typer.Option(
        "both", "--export", "-e", help="Export format: 'csv', 'xlsx', or 'both'"
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output-dir", help="Directory to save export files"
    ),
    cache_file: Path = typer.Option(
        Path("data/fixtures_cache.json"), "--cache-file", help="Path to fixtures cache"
    ),
    url: Optional[str] = typer.Option(
        None, "--url", "-u", help="Override association/fixture URL"
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use realistic offline mock data for testing"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
) -> None:
    """Run end-to-end pipeline: Fetch fixtures then Analyze ground availability."""
    setup_logging(verbose)
    console.rule("[bold cyan]PlayHQ Venue Tracker Pipeline[/bold cyan]")

    # Step 1: Fetch
    fetch(
        config_path=config_path,
        cache_file=cache_file,
        url=url,
        mock=mock,
        verbose=verbose,
    )

    # Step 2: Analyze
    analyze(
        config_path=config_path,
        cache_file=cache_file,
        start_date=start_date,
        end_date=end_date,
        slot=slot,
        afternoon_only=afternoon_only,
        limit=8,
        target_date=None,
        export=export,
        output_dir=output_dir,
        verbose=verbose,
    )


@app.command("find-venues")
def find_venues(
    lat: float = typer.Option(
        -37.82774221477076, "--lat", help="Center latitude (default: Laburnum CC)"
    ),
    lon: float = typer.Option(
        145.14233395005854, "--lon", help="Center longitude (default: Laburnum CC)"
    ),
    radius: float = typer.Option(
        10.0, "--radius", "-r", help="Radius in kilometers (default: 10.0)"
    ),
    max_grades: int = typer.Option(
        6, "--max-grades", help="Max grades to sample per competition (default: 6)"
    ),
    update_config: bool = typer.Option(
        False, "--update-config", "-u", help="Automatically populate config.yaml with discovered venues"
    ),
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml to update"
    ),
    output_json: Optional[Path] = typer.Option(
        None, "--output-json", help="Path to save raw venues JSON"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging"),
) -> None:
    """Discover cricket grounds within a radius of a coordinate and optionally populate config.yaml."""
    from playhq_venue_tracker.discovery import (
        PlayHQVenueFinder,
        generate_venue_configs,
        update_config_yaml,
    )
    from rich import box
    from rich.table import Table

    setup_logging(verbose)
    finder = PlayHQVenueFinder(center_lat=lat, center_lon=lon, radius_km=radius)
    venues = finder.discover_nearby_venues(max_grades_per_season=max_grades)

    if not venues:
        console.print("[bold red]No venues discovered within radius.[/bold red]")
        raise typer.Exit(code=1)

    table = Table(
        title=f"Cricket Grounds within {radius}km of ({lat:.4f}, {lon:.4f})",
        box=box.ROUNDED,
        header_style="bold bright_white on blue",
    )
    table.add_column("Distance", justify="right", style="cyan", width=10)
    table.add_column("Venue Name", justify="left", style="bold green")
    table.add_column("Suburb", justify="left", style="yellow", width=16)
    table.add_column("Surfaces / Ovals", justify="left", width=22)
    table.add_column("Home Club / Tenant", justify="left", style="bold bright_yellow", width=20)
    table.add_column("Teams & Comp", justify="left", style="dim")

    for v in venues:
        surfs_str = ", ".join(v["surfaces"]) if v["surfaces"] else "[dim]Single Ground[/dim]"
        club_str = v.get("home_club") or "[dim]Unassigned[/dim]"
        teams_list = v.get("home_teams") or []
        comp_str = f" [{v['competition']}]" if v.get("competition") else ""
        teams_str = (", ".join(teams_list) + comp_str) if teams_list else (comp_str.strip() or "[dim]N/A[/dim]")

        table.add_row(
            f"{v['distance_km']:.2f} km",
            v["name"],
            v["suburb"] or "Unknown",
            surfs_str,
            club_str,
            teams_str,
        )

    console.print()
    console.print(table)
    console.print(f"\nTotal venues discovered: [bold green]{len(venues)}[/bold green]")

    venue_configs = generate_venue_configs(venues)
    console.print(f"Total individual grounds/ovals mapped: [bold green]{len(venue_configs)}[/bold green]")

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(venues, f, indent=2)
        console.print(f"Saved raw venues data to: [bold]{output_json}[/bold]")

    if update_config:
        update_config_yaml(config_path, venue_configs, radius, (lat, lon))
    else:
        console.print("\n[yellow]Tip: Re-run with --update-config to automatically populate config.yaml[/yellow]")


if __name__ == "__main__":
    app()
