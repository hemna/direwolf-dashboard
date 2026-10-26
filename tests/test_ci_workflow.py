"""Regression tests for the CI gates (issue #179).

The audited workflow ended its JavaScript check in ``|| true``, capped it at 50
files, and never ran pytest. These tests read the workflow and execute its
JavaScript step so both gaps stay closed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parent.parent / ".forgejo" / "workflows" / "ci.yml"


def _steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["lint"]["steps"]


def _run_block(name: str) -> str:
    for step in _steps():
        if step.get("name") == name:
            return step["run"]
    raise AssertionError(f"workflow has no {name!r} step")


def test_workflow_runs_the_test_suite():
    runs = " ".join(step.get("run", "") for step in _steps())
    assert "pytest" in runs
    assert ".venv/bin/activate" in _run_block("Run tests")


def test_js_syntax_check_fails_for_an_invalid_file_past_fifty(tmp_path):
    """The JS gate must propagate failures and cover every first-party file."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    script = _run_block("JS syntax check")
    assert "|| true" not in script

    for i in range(51):
        (tmp_path / f"ok{i:02d}.js").write_text("const a = 1;\n")
    (tmp_path / "zz_broken.js").write_text("function broken( {\n")

    result = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True
    )

    assert result.returncode != 0, result.stdout + result.stderr
