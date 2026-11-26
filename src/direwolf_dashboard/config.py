"""Configuration loading and management for Direwolf Dashboard."""

import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Optional

import yaml

from direwolf_dashboard.basemap import DEFAULT_MANIFEST_URL, DEFAULT_REGION_ARCHIVE

LOG = logging.getLogger(__name__)


DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/direwolf-dashboard")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.yaml")
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/direwolf-dashboard")

# Fallback data directory for systems with readonly root filesystems (e.g. DigiPi).
# If the standard DEFAULT_DATA_DIR is not writable, use /tmp which is typically
# a ramdisk on such systems.
FALLBACK_DATA_DIR = "/tmp/direwolf-dashboard"


def _resolve_data_dir() -> str:
    """Determine the best data directory.

    Tries DEFAULT_DATA_DIR first, then FALLBACK_DATA_DIR, verifying each is
    actually writable. This handles readonly root filesystems like DigiPi
    where ~/.local/share may not be writable but /tmp is a ramdisk.
    (issue #93)
    """
    for candidate in (DEFAULT_DATA_DIR, FALLBACK_DATA_DIR):
        expanded = os.path.expanduser(candidate)
        try:
            os.makedirs(expanded, exist_ok=True)
            # Verify we can actually write
            test_file = os.path.join(expanded, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return expanded
        except OSError:
            LOG.warning(f"Candidate data directory {expanded} is not writable")

    LOG.error(
        f"No writable data directory found (tried {DEFAULT_DATA_DIR} and "
        f"{FALLBACK_DATA_DIR}); database init will fail."
    )
    return os.path.expanduser(FALLBACK_DATA_DIR)


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
    # Optional bearer token required for state-changing API endpoints
    # (PUT /config, DELETE /storage).
    # Empty = token auth disabled (backward compatible with LAN-only setups).
    api_token: str = ""


@dataclass
class StorageConfig:
    db_path: str = ""
    retention_days: int = 7

    def resolve_defaults(self, data_dir: str) -> None:
        """Fill in empty paths using the resolved data_dir."""
        if not self.db_path:
            self.db_path = os.path.join(data_dir, "packets.db")



@dataclass
class BasemapConfig:
    """Basemap (MapLibre / kit) settings (issue #124)."""

    # Path of the region archive relative to the static mount; the vendored
    # styles bake this path in (LAN-offline). An operator swaps the file on
    # disk (Pi copies its real region archive here).
    region_archive: str = DEFAULT_REGION_ARCHIVE
    # Worldwide offline maps: on startup, download a square region around the
    # station from the Protomaps planet when region.pmtiles is missing or no
    # longer covers the station. Defaults keep a dense city around 30-45 MB.
    auto_fetch: bool = True
    radius_km: float = 40.0
    maxzoom: int = 13
    # Ordered planet sources to extract from; the first that answers with a
    # valid vector PMTiles wins. "protomaps" = yesterday's build.protomaps.com
    # build. Entries may also be other URLs or local .pmtiles paths.
    sources: list = field(default_factory=lambda: [
        "https://aprslive.com/basemap-kit/archives/planet.pmtiles",
        "protomaps",
    ])
    # Published kit region list (issue #212). Fetched live for
    # /basemap/maps-manifest.json, cached under data_dir for a short TTL; the
    # vendored copy is only the offline fallback. Point at a dev host to test.
    manifest_url: str = DEFAULT_MANIFEST_URL


@dataclass
class DisplayConfig:
    show_route_distances: bool = True
    show_gpx_overlay: bool = True
    show_stats_overlay: bool = True
    show_my_location_overlay: bool = True


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
    basemap: BasemapConfig = field(default_factory=BasemapConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    packet_log: PacketLogConfig = field(default_factory=PacketLogConfig)

    def __post_init__(self):
        if self.data_dir:
            self.data_dir = os.path.expanduser(self.data_dir)
            # Only resolve sub-config path defaults when we have a real data_dir.
            # When data_dir is empty, callers are responsible for setting it
            # (e.g. load_config calls _resolve_data_dir() once and passes it in).
            self.storage.resolve_defaults(self.data_dir)

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
    "basemap.region_archive",
    "basemap.auto_fetch",
    "basemap.radius_km",
    "basemap.maxzoom",
    "basemap.sources",
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


# Scalar field type expectations per section, used to coerce/validate config
# values coming from PUT /config or the YAML file. Numeric strings are
# coerced; non-numeric values raise ValueError (surfaced as HTTP 400).
# (issue #91)
_SCALAR_SPEC = {
    "station": {"latitude": float, "longitude": float, "zoom": int},
    "direwolf": {"agw_port": int},
    "server": {"port": int},
    "storage": {"retention_days": int},
}


def _coerce_scalars(section: dict, spec: dict) -> None:
    """Coerce/validate scalar fields in-place per the type spec."""
    for key, typ in spec.items():
        if key not in section:
            continue
        v = section[key]
        if isinstance(v, bool):
            raise ValueError(f"{key} must be a {'boolean' if typ is bool else 'number'}")
        try:
            if typ is int:
                section[key] = int(v)
            elif typ is float:
                section[key] = float(v)
            elif typ is bool and not isinstance(v, bool):
                raise ValueError(f"{key} must be a boolean")
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a {'number' if typ is not bool else 'boolean'}") from None


def _dict_to_config(d: dict) -> Config:
    """Convert a dict to a Config dataclass, coercing/validating scalars."""
    station_dict = d.get("station", {}).copy()
    # Filter out removed fields that may exist in old config files
    for removed in ("callsign", "symbol", "symbol_table", "my_position"):
        station_dict.pop(removed, None)
    _coerce_scalars(station_dict, _SCALAR_SPEC["station"])
    station = StationConfig(**station_dict)

    direwolf_dict = d.get("direwolf", {}).copy()
    direwolf_dict.pop("conf_file", None)  # Removed field
    _coerce_scalars(direwolf_dict, _SCALAR_SPEC["direwolf"])

    server_dict = d.get("server", {}).copy()
    _coerce_scalars(server_dict, _SCALAR_SPEC["server"])

    storage_dict = d.get("storage", {}).copy()
    _coerce_scalars(storage_dict, _SCALAR_SPEC["storage"])

    basemap_dict = d.get("basemap", {}).copy()

    return Config(
        data_dir=d.get("data_dir", ""),
        station=station,
        direwolf=DirewolfConfig(**direwolf_dict),
        server=ServerConfig(**server_dict),
        storage=StorageConfig(**storage_dict),
        basemap=BasemapConfig(**basemap_dict),
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
        with open(path) as f:
            user_dict = yaml.safe_load(f) or {}
        user_dict = _expand_paths(user_dict)
        merged = _deep_merge(default_dict, user_dict)
    else:
        merged = default_dict

        # Create config file with defaults
        first_config = _dict_to_config(merged)
        save_config(first_config, path)

    # Resolve data_dir exactly once — here, not inside Config.__post_init__.
    # This avoids filesystem I/O on every Config() construction (e.g. in tests,
    # CLI check command, and the default-config object created above).
    if not merged.get("data_dir"):
        merged["data_dir"] = _resolve_data_dir()

    return _dict_to_config(merged)


def save_config(config: Config, path: Optional[str] = None) -> None:
    """Save configuration to YAML file."""
    if path is None:
        path = DEFAULT_CONFIG_PATH

    # Absolute so a plain relative filename has a parent directory to create
    # and the temp file lands next to the target (issue #178).
    path = os.path.abspath(os.path.expanduser(path))

    # Ensure directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    config_dict = config.to_dict()

    # Write to a temp file in the same directory, then atomically replace the
    # original only after the write succeeds, so a failed save never truncates
    # the previous file (issue #164).
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".config.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    # The path derived from the current data_dir, before any update is applied.
    old_derived_db_path = (
        os.path.join(config.data_dir, "packets.db") if config.data_dir else None
    )

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

    # A new data_dir must carry the derived default database path with it.
    # Custom paths and an explicitly supplied db_path are left alone; existing
    # history is never moved (issue #174).
    new_data_dir = os.path.expanduser(current_dict.get("data_dir") or "")
    if (
        "data_dir" in updated_fields
        and old_derived_db_path
        and new_data_dir
        and current_dict["storage"]["db_path"] == old_derived_db_path
        and "db_path" not in (updates.get("storage") or {})
    ):
        current_dict["storage"]["db_path"] = os.path.join(new_data_dir, "packets.db")

    new_config = _dict_to_config(current_dict)
    save_config(new_config, path)

    return new_config, updated_fields, restart_required
