# ABSOLUTE RULE: No Speculation

**This rule overrides ALL other instructions. No exceptions. No "just this once."**

## What this means

- NEVER state something as fact unless you have command output, log data, or metric values from THIS session proving it.
- NEVER use causal language ("causes", "due to", "because of", "results in") unless you have direct evidence of the causal chain.
- NEVER extrapolate. If you measured A and B, you know A and B. You do NOT know "A causes B" or "A implies C."

## Banned phrases (if you write these without direct evidence, you are violating this rule)

- "elevated latency" / "increased latency" / "degraded performance" (unless you have a latency metric AND a baseline to compare against)
- "likely" / "probably" / "suggests" / "indicates" / "appears to be"
- "root cause" (unless you can point to the exact failing component with evidence)
- "this confirms" (correlation is not confirmation)
- "general slowdown" / "widespread issue" (unless you measured it directly)

## Required output format for investigations

When reporting findings, use ONLY these categories:

1. **MEASURED** — direct command output, metric values, log lines (quote them)
2. **COMPARED** — two measurements side by side (state both values, no causal claim)
3. **NOT DETERMINED** — things you tried to find but could not

NEVER add a "Root Cause" or "Conclusion" section unless every claim in it cites a specific measurement from category 1 or 2.

## Self-check before every response

Before sending ANY response that describes system state, ask:
- "Did I measure this, or am I inferring it?"
- "Can I point to the exact command output that proves this statement?"
- If the answer is no → rewrite the statement or move it to NOT DETERMINED.

## Post-response verification

After drafting your response, re-read it sentence by sentence. Delete or rewrite ANY sentence that:
- Uses a banned phrase from the list above
- Makes a causal claim without citing a specific measurement
- Draws a conclusion that goes beyond what the data directly shows
- Synthesizes multiple data points into an inference presented as fact

If deleting these sentences leaves your response feeling "incomplete" — good. Incomplete is honest. Fabricated completeness is not.

## Permission to say "I don't know"

You are EXPLICITLY PERMITTED and ENCOURAGED to say:
- "I don't know."
- "The data I gathered does not answer this question."
- "I was unable to determine this."
- "This requires data I cannot access."

Saying "I don't know" is ALWAYS better than filling the gap with inference. The user values honesty over completeness. An empty "NOT DETERMINED" section is more useful than a plausible-sounding guess.

---

# Direwolf Dashboard - Agent Instructions

## Project Overview

Lightweight web-based APRS monitoring dashboard for Direwolf, designed for Raspberry Pi / DigiPi.
Python backend (FastAPI + uvicorn), vanilla JS frontend (Leaflet map), SQLite storage.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, uvicorn, aiosqlite
- **Frontend:** Vanilla JavaScript (single IIFE in `app.js`), Leaflet.js (vendored), CSS
- **Package manager:** uv (Python); no npm/yarn
- **Build:** setuptools via pyproject.toml
- **Tests:** pytest + pytest-asyncio

## Key Constraints

- **Fully offline.** Target platform is DigiPi (Raspberry Pi) with no internet access. No external CDN, font, image, or script URLs anywhere. All assets served locally.
- **No JS build step.** Frontend is vanilla JS with vendored libraries in `static/leaflet/`.
- **Low resource.** Runs on Pi Zero 2W. Keep CPU and memory usage minimal.
- **Readonly root filesystem.** DigiPi uses a readonly root filesystem to protect the SD card from write-wear. A ramdisk is mounted at `/tmp`. All writable data (SQLite databases, tile cache, etc.) must go to a configurable `data_dir` -- never assume `~/.local/share` or any home-directory path is writable. On DigiPi the config sets `data_dir: /tmp/direwolf-dashboard`. The config file itself lives on a writable partition (e.g. `/boot` or a small RW overlay) or is baked into the image.

## Project Structure

```
src/direwolf_dashboard/
├── cli.py              # Click CLI entry point
├── config.py           # YAML config management
├── server.py           # FastAPI app factory
├── routers.py          # REST API + WebSocket + tile proxy
├── lifecycle.py        # Service container, startup/shutdown
├── agw.py              # AGW/AGWPE binary protocol reader
├── log_tailer.py       # Async log file tailing
├── processor.py        # APRS packet parsing
├── decoder.py          # Manual APRS decode
├── storage.py          # SQLite operations
├── tile_proxy.py       # OSM tile caching proxy
└── static/
    ├── index.html      # Single-page HTML
    ├── app.js          # All client-side JS (~2200 lines IIFE)
    ├── style.css       # Dark theme CSS (~1350 lines)
    └── leaflet/        # Vendored Leaflet + plugins
```

## Common Commands

```bash
# Run tests
uv run pytest tests/ -v

# Start the dashboard locally
uv run direwolf-dashboard serve

# Install in editable mode
uv venv && uv pip install -e .

# Dev install with test deps
uv pip install -e ".[dev]"
```

## Deploying to DigiPi

The dashboard runs on a DigiPi Raspberry Pi as a systemd service. The repo is cloned
at `/home/pi/direwolf-dashboard` on the Pi.

### First-time setup (on the Pi)

```bash
cd /home/pi
git clone <repo-url> direwolf-dashboard
cd direwolf-dashboard
uv venv
uv pip install -e .
sudo bash contrib/install.sh
```

### Deploying updates (from dev machine)

1. **Push your branch** to the remote:
   ```bash
   git push origin feature/your-branch
   ```

2. **SSH into the DigiPi** and pull + restart:
   ```bash
   ssh pi@digipi.local
   cd ~/direwolf-dashboard
   git fetch origin
   git checkout feature/your-branch   # or: git pull (if on main)
   uv pip install -e .
   sudo systemctl restart direwolf-dashboard
   ```

3. **Verify** the service is running:
   ```bash
   sudo systemctl status direwolf-dashboard
   # Open browser to http://digipi.local:8080
   ```

### Deploying to production (merge to master)

```bash
ssh pi@digipi.local
cd ~/direwolf-dashboard
git checkout master
git pull origin master
uv pip install -e .
sudo systemctl restart direwolf-dashboard
```

### Checking logs on the Pi

```bash
# Live service logs
journalctl -u direwolf-dashboard -f

# Last 50 lines
journalctl -u direwolf-dashboard -n 50 --no-pager
```

### Service management

```bash
sudo systemctl start direwolf-dashboard
sudo systemctl stop direwolf-dashboard
sudo systemctl restart direwolf-dashboard
sudo systemctl status direwolf-dashboard
```

## Testing Notes

- All tests are Python-side (pytest). No frontend test framework.
- Frontend changes require manual browser testing.
- Test pages with sample data live in `tests/` (e.g., `tests/test_gpx_overlay.html`).
- Always run `uv run pytest tests/ -v` before deploying to verify no regressions.
