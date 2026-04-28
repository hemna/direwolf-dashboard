"""Tests for configuration loading and management."""

import os
from unittest import mock

import pytest
import yaml

from direwolf_dashboard.config import (
    Config,
    StationConfig,
    DirewolfConfig,
    ServerConfig,
    StorageConfig,
    TilesConfig,
    load_config,
    save_config,
    update_config,
    RESTART_REQUIRED_FIELDS,
)


class TestStationConfigSimplified:
    """Test StationConfig after removing callsign/symbol/my_position fields."""

    def test_station_config_has_no_callsign(self):
        config = Config()
        assert not hasattr(config.station, "callsign")

    def test_station_config_has_no_symbol(self):
        config = Config()
        assert not hasattr(config.station, "symbol")
        assert not hasattr(config.station, "symbol_table")

    def test_station_config_has_no_my_position(self):
        config = Config()
        assert not hasattr(config.station, "my_position")

    def test_config_to_dict_excludes_my_position(self):
        config = Config()
        d = config.to_dict()
        assert "my_position" not in d["station"]


class TestDefaultConfig:
    """Test default configuration generation."""

    def test_default_config_has_all_sections(self):
        config = Config()
        assert isinstance(config.station, StationConfig)
        assert isinstance(config.direwolf, DirewolfConfig)
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.storage, StorageConfig)
        assert isinstance(config.tiles, TilesConfig)

    def test_default_station_latitude(self):
        config = Config()
        assert config.station.latitude == 0.0

    def test_default_server_port(self):
        config = Config()
        assert config.server.port == 8080

    def test_default_retention_days(self):
        config = Config()
        assert config.storage.retention_days == 7

    def test_default_tile_cache_mode(self):
        config = Config()
        assert config.tiles.cache_mode == "lazy"

    def test_default_agw_port(self):
        config = Config()
        assert config.direwolf.agw_port == 8000

    def test_to_dict(self):
        config = Config()
        d = config.to_dict()
        assert isinstance(d, dict)
        assert "station" in d
        assert "direwolf" in d
        assert "server" in d
        assert "storage" in d
        assert "tiles" in d
        assert d["station"]["latitude"] == 0.0


class TestLoadSaveConfig:
    """Test YAML load/save roundtrip."""

    def test_save_and_load_roundtrip(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        config.station.latitude = 37.75
        config.station.longitude = -77.45
        config.server.port = 9090

        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.station.latitude == 37.75
        assert loaded.station.longitude == -77.45
        assert loaded.server.port == 9090

    def test_load_creates_default_if_missing(self, tmp_path):
        config_path = str(tmp_path / "subdir" / "config.yaml")
        assert not os.path.exists(config_path)

        config = load_config(config_path)

        assert os.path.exists(config_path)
        assert config.station.latitude == 0.0

    def test_partial_config_merges_with_defaults(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")

        # Write a partial config — only station section
        partial = {"station": {"latitude": 37.75}}
        with open(config_path, "w") as f:
            yaml.dump(partial, f)

        config = load_config(config_path)

        # User value should be applied
        assert config.station.latitude == 37.75
        # Defaults should fill in the rest
        assert config.station.longitude == 0.0
        assert config.server.port == 8080
        assert config.storage.retention_days == 7

    def test_old_config_with_removed_fields_loads(self, tmp_path):
        """Old config files with callsign/symbol/my_position/conf_file should load fine."""
        config_path = str(tmp_path / "config.yaml")

        old_config = {
            "station": {
                "callsign": "WB4BOR",
                "latitude": 37.75,
                "longitude": -77.45,
                "symbol": "#",
                "symbol_table": "S",
                "zoom": 14,
                "my_position": {
                    "type": "station",
                    "callsign": "WB4BOR",
                },
            },
            "direwolf": {
                "agw_host": "localhost",
                "agw_port": 8000,
                "log_file": "/var/log/direwolf/direwolf.log",
                "conf_file": "/home/pi/direwolf.conf",
            },
        }
        with open(config_path, "w") as f:
            yaml.dump(old_config, f)

        config = load_config(config_path)

        # Removed fields should be silently ignored
        assert not hasattr(config.station, "callsign")
        assert not hasattr(config.station, "symbol")
        assert not hasattr(config.station, "my_position")
        # Preserved fields should work
        assert config.station.latitude == 37.75
        assert config.station.longitude == -77.45
        assert config.station.zoom == 14
        assert not hasattr(config.direwolf, "conf_file")
        assert config.direwolf.agw_host == "localhost"

    def test_path_expansion(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")

        config_with_tilde = {
            "storage": {"db_path": "~/mydata/packets.db"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_with_tilde, f)

        config = load_config(config_path)

        assert "~" not in config.storage.db_path
        assert config.storage.db_path.startswith("/")

    def test_saved_yaml_is_valid(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict)
        assert "station" in data
        assert d["station"]["latitude"] == 0.0 if (d := data) else False


class TestUpdateConfig:
    """Test partial config updates."""

    def test_update_hot_reload_field(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config, {"storage": {"retention_days": 14}}, config_path
        )

        assert new_config.storage.retention_days == 14
        assert "storage.retention_days" in updated
        assert restart is False

    def test_update_restart_required_field(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config, {"server": {"port": 9090}}, config_path
        )

        assert new_config.server.port == 9090
        assert "server.port" in updated
        assert restart is True

    def test_update_persists_to_yaml(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        update_config(config, {"station": {"latitude": 37.75}}, config_path)

        reloaded = load_config(config_path)
        assert reloaded.station.latitude == 37.75

    def test_update_no_change(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config, {"station": {"latitude": 0.0}}, config_path
        )

        assert updated == []
        assert restart is False

    def test_update_multiple_fields(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config,
            {
                "station": {"latitude": 37.75, "longitude": -77.45},
                "storage": {"retention_days": 30},
            },
            config_path,
        )

        assert new_config.station.latitude == 37.75
        assert new_config.station.longitude == -77.45
        assert new_config.storage.retention_days == 30
        assert len(updated) == 3
        assert restart is False

    def test_update_ignores_my_position_in_yaml(self, tmp_path):
        """my_position in station updates should be silently ignored by update_config.

        my_position is now stored in the DB, not the YAML config.
        """
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        # my_position key should be harmless -- it's just an unknown key
        # that gets ignored since StationConfig doesn't have it
        new_config, updated, restart = update_config(
            config,
            {"station": {"latitude": 37.75}},
            config_path,
        )

        assert new_config.station.latitude == 37.75
        assert not hasattr(new_config.station, "my_position")
