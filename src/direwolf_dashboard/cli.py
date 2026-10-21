"""CLI entry point for Direwolf Dashboard."""

import logging
import os
import shutil
import subprocess

import click

from direwolf_dashboard import __version__
from direwolf_dashboard.basemap import (
    FETCHED_REGION_ARCHIVE,
    bbox_around,
    build_extract_command,
    default_planet_url,
    parse_bbox,
    sha256_file,
)
from direwolf_dashboard.config import DEFAULT_CONFIG_PATH, load_config

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=__version__)
@click.option(
    "--config",
    "-c",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
    type=click.Path(),
)
@click.pass_context
def main(ctx, config_path):
    """Direwolf Dashboard - Live display of Direwolf activity."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.command()
@click.pass_context
def serve(ctx):
    """Start the Direwolf Dashboard web server."""
    import uvicorn

    # Configure app-level logging so our LOG.info() calls are visible
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = ctx.obj["config_path"]
    config = load_config(config_path)

    click.echo(f"Direwolf Dashboard v{__version__}")
    click.echo(f"Config: {config_path}")
    click.echo(f"Listening on http://{config.server.host}:{config.server.port}")

    # Import here to avoid circular imports and speed up --help
    from direwolf_dashboard.server import create_app

    app = create_app(config, config_path)

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        loop="asyncio",
        http="h11",
        ws="websockets",
        ws_ping_interval=None,
        ws_per_message_deflate=False,
    )


@main.command()
@click.pass_context
def check(ctx):
    """Validate config and test Direwolf connectivity."""
    import socket

    config_path = ctx.obj["config_path"]

    try:
        config = load_config(config_path)
        click.secho("Config loaded OK", fg="green")
    except Exception as e:
        click.secho(f"Config error: {e}", fg="red")
        raise SystemExit(1) from e

    click.echo(
        f"  Station lat/lon: {config.station.latitude}, {config.station.longitude}"
    )
    click.echo(f"  Zoom: {config.station.zoom}")
    if config.station.latitude and config.station.longitude:
        click.echo(f"  Fallback position: {config.station.latitude}, {config.station.longitude}")
    else:
        click.echo("  Fallback position: not set")
    click.echo("  My Position: stored in DB (set via web UI)")
    click.echo(f"  AGW: {config.direwolf.agw_host}:{config.direwolf.agw_port}")
    click.echo(f"  Log file: {config.direwolf.log_file}")
    click.echo(f"  Web server: {config.server.host}:{config.server.port}")
    click.echo(f"  DB: {config.storage.db_path}")

    # Test AGW connectivity
    click.echo()
    click.echo("Testing AGW connection...")
    try:
        sock = socket.create_connection(
            (config.direwolf.agw_host, config.direwolf.agw_port),
            timeout=5,
        )
        sock.close()
        click.secho("  AGW connection OK", fg="green")
    except OSError as e:
        click.secho(f"  AGW connection FAILED: {e}", fg="red")

    # Test log file
    click.echo("Testing log file...")
    import os

    if os.path.exists(config.direwolf.log_file):
        click.secho(f"  Log file exists: {config.direwolf.log_file}", fg="green")
    else:
        click.secho(f"  Log file not found: {config.direwolf.log_file}", fg="yellow")
        click.echo("  (Dashboard will retry when file appears)")


@main.command()
def version():
    """Show the version."""
    click.echo(f"Direwolf Dashboard v{__version__}")


@main.group()
def basemap():
    """Fetch and manage the offline basemap region archive."""


@basemap.command()
@click.option(
    "--bbox",
    "bbox_text",
    metavar="MIN_LON,MIN_LAT,MAX_LON,MAX_LAT",
    default=None,
    help="Bounding box to extract; overrides the --radius-km box around the station.",
)
@click.option(
    "--radius-km",
    "radius_km",
    type=float,
    default=75.0,
    show_default=True,
    help="Radius (km) of the square bbox around config.station, used when --bbox is absent.",
)
@click.option(
    "--maxzoom",
    "maxzoom",
    type=int,
    default=15,
    show_default=True,
    help="Maximum zoom level to extract.",
)
@click.option(
    "--planet",
    "planet",
    default=None,
    help="Protomaps planet .pmtiles URL (default: yesterday's UTC build).",
)
@click.option(
    "--output",
    "output",
    default=None,
    type=click.Path(),
    help="Output .pmtiles path (default: <data_dir>/basemap/region.pmtiles).",
)
@click.pass_context
def fetch(ctx, bbox_text, radius_km, maxzoom, planet, output):
    """Extract a basemap region around the station from a Protomaps planet.

    Needs internet (and the pmtiles CLI) once, at fetch time; serving the
    resulting archive stays LAN-offline. (issue #132)
    """
    config = load_config(ctx.obj["config_path"])

    if bbox_text:
        try:
            bbox = parse_bbox(bbox_text)
        except ValueError as e:
            raise click.ClickException(str(e)) from None
    else:
        lat, lon = config.station.latitude, config.station.longitude
        if not lat and not lon:
            raise click.ClickException(
                "No station position configured (station.latitude/longitude are 0,0) "
                "and no --bbox given. Pass --bbox=MIN_LON,MIN_LAT,MAX_LON,MAX_LAT or set "
                "station.latitude/longitude in the config."
            )
        bbox = bbox_around(lat, lon, radius_km)

    if planet is None:
        planet = default_planet_url()

    if output is None:
        output = os.path.join(config.data_dir, FETCHED_REGION_ARCHIVE)
    output = os.path.abspath(os.path.expanduser(output))

    pmtiles_bin = shutil.which("pmtiles")
    if not pmtiles_bin:
        raise click.ClickException(
            "The 'pmtiles' CLI is required to fetch a region but was not found on PATH.\n"
            "Install it from https://github.com/protomaps/go-pmtiles "
            "(e.g. 'go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest')."
        )

    os.makedirs(os.path.dirname(output), exist_ok=True)
    tmp = output + ".tmp.pmtiles"
    cmd = build_extract_command(planet, tmp, bbox, maxzoom)

    click.echo(f"Downloading bbox={','.join(f'{v:.6f}' for v in bbox)} maxzoom={maxzoom}")
    click.echo(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"pmtiles extract failed with exit code {e.returncode}") from e

    os.replace(tmp, output)  # atomic: never leave a partial region.pmtiles

    size = os.path.getsize(output)
    click.echo(f"Wrote {output}")
    click.echo(f"  size: {size:,} bytes")
    click.echo(f"  sha256: {sha256_file(output)}")
