"""Configuration loading and management for Direwolf Dashboard."""

import os
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
