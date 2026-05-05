"""CLI entry point for Direwolf Dashboard."""

import logging

import click

from direwolf_dashboard import __version__
from direwolf_dashboard.config import load_config, DEFAULT_CONFIG_PATH


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


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
    import asyncio
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
        ws="websockets",
        ws_ping_interval=None,
        ws_per_message_deflate=False,
    )


@main.command()
@click.pass_context
def check(ctx):
    """Validate config and test Direwolf connectivity."""
    import asyncio
    import socket

    config_path = ctx.obj["config_path"]

    try:
        config = load_config(config_path)
        click.secho("Config loaded OK", fg="green")
    except Exception as e:
        click.secho(f"Config error: {e}", fg="red")
        raise SystemExit(1)

    click.echo(
        f"  Station lat/lon: {config.station.latitude}, {config.station.longitude}"
    )
    click.echo(f"  Zoom: {config.station.zoom}")
    if config.station.latitude and config.station.longitude:
        click.echo(f"  Fallback position: {config.station.latitude}, {config.station.longitude}")
    else:
        click.echo(f"  Fallback position: not set")
    click.echo(f"  My Position: stored in DB (set via web UI)")
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
    except (socket.error, OSError) as e:
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
