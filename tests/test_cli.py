"""Tests for cli.py — verify the serve command wiring."""

from pathlib import Path

CLI_SRC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "cli.py"


def test_serve_uses_uvicorn_run():
    """serve() must delegate to uvicorn.run()."""
    src = CLI_SRC.read_text(encoding="utf-8")
    assert "uvicorn.run(" in src


def test_no_manual_event_loop_setup():
    """The ineffective manual event-loop/executor setup was removed (issue #77).

    uvicorn.run() uses asyncio.run internally, which always creates a new
    event loop, so set_default_executor on a pre-created loop was dead code.
    """
    src = CLI_SRC.read_text(encoding="utf-8")
    assert "set_default_executor" not in src
    assert "new_event_loop" not in src
    assert "ThreadPoolExecutor" not in src
