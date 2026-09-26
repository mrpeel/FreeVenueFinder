#!/usr/bin/env python3
"""Convenience script to find PlayHQ cricket grounds within a radius of Laburnum CC."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from rich import box
from rich.console import Console
from rich.table import Table

from playhq_venue_tracker.discovery import (
    DEFAULT_LAT,
    DEFAULT_LON,
    DEFAULT_RADIUS_KM,
    PlayHQVenueFinder,
    generate_venue_configs,
    update_config_yaml,
)

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Find cricket grounds within radius of Laburnum CC on PlayHQ.")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT, help="Center latitude (default: Laburnum CC)")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON, help="Center longitude (default: Laburnum CC)")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM, help="Radius in km (default: 10.0)")
    parser.add_argument("--max-grades", type=int, default=6, help="Max grades to sample per competition (default: 6)")
    parser.add_argument("--update-config", action="store_true", help="Automatically update config.yaml with discovered venues")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to config.yaml to update")
    parser.add_argument("--output-json", type=Path, default=None, help="Save discovered venues to a JSON file")

    args = parser.parse_args()

    finder = PlayHQVenueFinder(center_lat=args.lat, center_lon=args.lon, radius_km=args.radius)
    venues = finder.discover_nearby_venues(max_grades_per_season=args.max_grades)

    if not venues:
        console.print("[bold red]No venues discovered within radius.[/bold red]")
        sys.exit(1)

    table = Table(
        title=f"Cricket Grounds within {args.radius}km of Laburnum CC ({args.lat:.4f}, {args.lon:.4f})",
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

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(venues, f, indent=2)
        console.print(f"Saved raw venues data to: [bold]{args.output_json}[/bold]")

    if args.update_config:
        update_config_yaml(args.config, venue_configs, args.radius, (args.lat, args.lon))
    else:
        console.print("\n[yellow]Tip: Re-run with --update-config to automatically populate config.yaml[/yellow]")


if __name__ == "__main__":
    main()
