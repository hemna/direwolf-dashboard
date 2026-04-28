"""Configuration loading and management for Direwolf Dashboard."""

import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

import yaml

LOG = logging.getLogger(__name__)


DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/direwolf-dashboard")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.yaml")
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/direwolf-dashboard")


@dataclass
class StationConfig:
    latitude: float = 0.0
    longitude: float = 0.0
    zoom: int = 12


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

    def resolve_defaults(self, data_dir: str) -> None:
        """Fill in empty paths using the resolved data_dir."""
        if not self.db_path:
            self.db_path = os.path.join(data_dir, "packets.db")


@dataclass
class TilesConfig:
    cache_dir: str = ""
    cache_mode: str = "lazy"  # "lazy" or "preload"
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    max_cache_mb: int = 500

    def resolve_defaults(self, data_dir: str) -> None:
        """Fill in empty paths using the resolved data_dir."""
        if not self.cache_dir:
            self.cache_dir = os.path.join(data_dir, "tiles")


@dataclass
class DisplayConfig:
    show_route_distances: bool = True
    show_gpx_overlay: bool = True
    show_stats_overlay: bool = True


@dataclass
class PacketLogConfig:
    show_timestamps: bool = False


@dataclass
class Config:
    data_dir: str = ""
    station: StationConfig = field(default_factory=StationConfig)
    direwolf: DirewolfConfig = field(default_factory=DirewolfConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    tiles: TilesConfig = field(default_factory=TilesConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    packet_log: PacketLogConfig = field(default_factory=PacketLogConfig)

    def __post_init__(self):
        if not self.data_dir:
            self.data_dir = DEFAULT_DATA_DIR
        self.data_dir = os.path.expanduser(self.data_dir)
        # Resolve sub-config defaults that depend on data_dir
        self.storage.resolve_defaults(self.data_dir)
        self.tiles.resolve_defaults(self.data_dir)

    def to_dict(self) -> dict:
        """Convert config to a JSON-serializable dict."""
        return asdict(self)


# Fields that require a restart when changed
RESTART_REQUIRED_FIELDS = {
    "data_dir",
    "server.host",
    "server.port",
    "direwolf.agw_host",
    "direwolf.agw_port",
    "direwolf.log_file",
    "storage.db_path",
    "tiles.cache_dir",
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
    station_dict = d.get("station", {}).copy()
    # Filter out removed fields that may exist in old config files
    for removed in ("callsign", "symbol", "symbol_table", "my_position"):
        station_dict.pop(removed, None)
    station = StationConfig(**station_dict)

    direwolf_dict = d.get("direwolf", {}).copy()
    direwolf_dict.pop("conf_file", None)  # Removed field

    return Config(
        data_dir=d.get("data_dir", ""),
        station=station,
        direwolf=DirewolfConfig(**direwolf_dict),
        server=ServerConfig(**d.get("server", {})),
        storage=StorageConfig(**d.get("storage", {})),
        tiles=TilesConfig(**d.get("tiles", {})),
        display=DisplayConfig(**d.get("display", {})),
        packet_log=PacketLogConfig(**d.get("packet_log", {})),
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
        first_config = _dict_to_config(merged)
        save_config(first_config, path)

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
