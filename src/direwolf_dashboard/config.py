"""Configuration loading and management for Direwolf Dashboard."""

import os
import re
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml


DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/direwolf-dashboard")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.yaml")
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/direwolf-dashboard")


@dataclass
class StationConfig:
    callsign: str = "N0CALL"
    latitude: float = 0.0
    longitude: float = 0.0
    symbol: str = "-"
    symbol_table: str = "/"


@dataclass
class DirewolfConfig:
    agw_host: str = "localhost"
    agw_port: int = 8000
    log_file: str = "/var/log/direwolf/direwolf.log"
    conf_file: str = ""


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class StorageConfig:
    db_path: str = ""
    retention_days: int = 7

    def __post_init__(self):
        if not self.db_path:
            self.db_path = os.path.join(DEFAULT_DATA_DIR, "packets.db")


@dataclass
class TilesConfig:
    cache_dir: str = ""
    cache_mode: str = "lazy"  # "lazy" or "preload"
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    max_cache_mb: int = 500

    def __post_init__(self):
        if not self.cache_dir:
            self.cache_dir = os.path.join(DEFAULT_DATA_DIR, "tiles")


@dataclass
class Config:
    station: StationConfig = field(default_factory=StationConfig)
    direwolf: DirewolfConfig = field(default_factory=DirewolfConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    tiles: TilesConfig = field(default_factory=TilesConfig)

    def to_dict(self) -> dict:
        """Convert config to a JSON-serializable dict."""
        return asdict(self)


# Fields that require a restart when changed
RESTART_REQUIRED_FIELDS = {
    "server.host",
    "server.port",
    "direwolf.agw_host",
    "direwolf.agw_port",
    "direwolf.log_file",
}


def _expand_paths(d: dict) -> dict:
    """Expand ~ in string values that look like paths."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _expand_paths(v)
        elif isinstance(v, str) and ("~" in v or v.startswith("/")):
            result[k] = os.path.expanduser(v)
        else:
            result[k] = v
    return result


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override values win."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _dict_to_config(d: dict) -> Config:
    """Convert a dict to a Config dataclass."""
    return Config(
        station=StationConfig(**d.get("station", {})),
        direwolf=DirewolfConfig(**d.get("direwolf", {})),
        server=ServerConfig(**d.get("server", {})),
        storage=StorageConfig(**d.get("storage", {})),
        tiles=TilesConfig(**d.get("tiles", {})),
    )


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration from YAML file, merging with defaults.

    If the file doesn't exist, creates it with defaults.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    path = os.path.expanduser(path)

    # Create default config
    default_config = Config()
    default_dict = default_config.to_dict()

    if os.path.exists(path):
        with open(path, "r") as f:
            user_dict = yaml.safe_load(f) or {}
        user_dict = _expand_paths(user_dict)
        merged = _deep_merge(default_dict, user_dict)
    else:
        merged = default_dict
        # Create config file with defaults
        save_config(default_config, path)

    return _dict_to_config(merged)


def save_config(config: Config, path: Optional[str] = None) -> None:
    """Save configuration to YAML file."""
    if path is None:
        path = DEFAULT_CONFIG_PATH

    path = os.path.expanduser(path)

    # Ensure directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    config_dict = config.to_dict()

    with open(path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)


def update_config(
    config: Config, updates: dict, path: Optional[str] = None
) -> tuple[Config, list[str], bool]:
    """Apply partial updates to config.

    Returns:
        (updated_config, list_of_updated_fields, restart_required)
    """
    current_dict = config.to_dict()
    updated_fields = []
    restart_required = False

    def _apply_updates(base: dict, updates: dict, prefix: str = ""):
        nonlocal restart_required
        for k, v in updates.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                _apply_updates(base[k], v, full_key)
            else:
                if k in base and base[k] != v:
                    base[k] = v
                    updated_fields.append(full_key)
                    if full_key in RESTART_REQUIRED_FIELDS:
                        restart_required = True

    _apply_updates(current_dict, updates)

    new_config = _dict_to_config(current_dict)
    save_config(new_config, path)

    return new_config, updated_fields, restart_required


# --- APRS symbol name mapping for Direwolf PBEACON "symbol" keyword ---
# Direwolf accepts human-readable names; map to APRS symbol char + table.
# See Direwolf User Guide, section on PBEACON symbol parameter.
DIREWOLF_SYMBOL_NAMES: dict[str, tuple[str, str]] = {
    # name -> (symbol_char, default_symbol_table)
    "digi": ("#", "/"),
    "house": ("-", "/"),
    "car": (">", "/"),
    "truck": ("k", "/"),
    "van": ("v", "/"),
    "motorcycle": ("<", "/"),
    "bicycle": ("b", "/"),
    "boat": ("s", "/"),
    "yacht": ("Y", "/"),
    "jogger": ("[", "/"),
    "dog": ("p", "/"),
    "balloon": ("O", "/"),
    "aircraft": ("^", "/"),
    "wx": ("_", "/"),
    "weather": ("_", "/"),
    "yagi": ("y", "/"),
    "igate": ("&", "/"),
    "phg": ("-", "/"),
}


def parse_direwolf_conf(conf_path: str) -> dict:
    """Parse a Direwolf configuration file and extract station settings.

    Extracts MYCALL, and from PBEACON lines: lat, long, symbol, overlay, comment.
    Prefers the first non-IG PBEACON if available, otherwise uses IG beacon.

    Returns a dict with keys matching StationConfig fields:
        callsign, latitude, longitude, symbol, symbol_table
    """
    conf_path = os.path.expanduser(conf_path)
    if not os.path.exists(conf_path):
        return {}

    result: dict = {}
    pbeacon_rf: Optional[dict] = None
    pbeacon_ig: Optional[dict] = None

    with open(conf_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # MYCALL
            if line.upper().startswith("MYCALL"):
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    result["callsign"] = parts[1].strip()
                continue

            # PBEACON
            if line.upper().startswith("PBEACON"):
                beacon = _parse_pbeacon(line)
                if beacon:
                    if beacon.get("_is_ig"):
                        if pbeacon_ig is None:
                            pbeacon_ig = beacon
                    else:
                        if pbeacon_rf is None:
                            pbeacon_rf = beacon

    # Prefer RF beacon over IG beacon
    beacon = pbeacon_rf or pbeacon_ig
    if beacon:
        if "latitude" in beacon:
            result["latitude"] = beacon["latitude"]
        if "longitude" in beacon:
            result["longitude"] = beacon["longitude"]
        if "symbol" in beacon:
            result["symbol"] = beacon["symbol"]
        if "symbol_table" in beacon:
            result["symbol_table"] = beacon["symbol_table"]

    return result


def _parse_pbeacon(line: str) -> Optional[dict]:
    """Parse a PBEACON line into a dict of extracted fields."""
    result: dict = {}

    # Remove the PBEACON keyword
    rest = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""

    # Parse key=value pairs (values may be quoted)
    # Use regex to handle: key=value, key="value with spaces"
    pairs = re.findall(r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))', rest)

    params: dict[str, str] = {}
    for key, quoted_val, plain_val in pairs:
        params[key.lower()] = quoted_val if quoted_val else plain_val

    # Check if this is an IG beacon
    result["_is_ig"] = params.get("sendto", "").upper() == "IG"

    # Latitude
    if "lat" in params:
        try:
            result["latitude"] = float(params["lat"])
        except ValueError:
            pass

    # Longitude
    if "long" in params:
        try:
            result["longitude"] = float(params["long"])
        except ValueError:
            pass

    # Symbol - Direwolf uses human-readable names or single chars
    sym_name = params.get("symbol", "").lower()
    overlay = params.get("overlay", "")

    if sym_name in DIREWOLF_SYMBOL_NAMES:
        sym_char, default_table = DIREWOLF_SYMBOL_NAMES[sym_name]
        result["symbol"] = sym_char
        if overlay and overlay != "0":
            # Overlay means use the overlay char as symbol_table
            result["symbol_table"] = overlay
        else:
            result["symbol_table"] = default_table
    elif len(sym_name) == 1:
        # Single character symbol
        result["symbol"] = sym_name
        if overlay and overlay != "0":
            result["symbol_table"] = overlay
        else:
            result["symbol_table"] = "/"

    return result if len(result) > 1 else None  # > 1 because _is_ig is always set
