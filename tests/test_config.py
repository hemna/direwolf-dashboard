"""Tests for configuration loading and management."""

import os
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


class TestDefaultConfig:
    """Test default configuration generation."""

    def test_default_config_has_all_sections(self):
        config = Config()
        assert isinstance(config.station, StationConfig)
        assert isinstance(config.direwolf, DirewolfConfig)
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.storage, StorageConfig)
        assert isinstance(config.tiles, TilesConfig)

    def test_default_station_callsign(self):
        config = Config()
        assert config.station.callsign == "N0CALL"

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
        assert d["station"]["callsign"] == "N0CALL"


class TestLoadSaveConfig:
    """Test YAML load/save roundtrip."""

    def test_save_and_load_roundtrip(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        config.station.callsign = "WB4BOR"
        config.station.latitude = 37.75
        config.station.longitude = -77.45
        config.server.port = 9090

        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.station.callsign == "WB4BOR"
        assert loaded.station.latitude == 37.75
        assert loaded.station.longitude == -77.45
        assert loaded.server.port == 9090

    def test_load_creates_default_if_missing(self, tmp_path):
        config_path = str(tmp_path / "subdir" / "config.yaml")
        assert not os.path.exists(config_path)

        config = load_config(config_path)

        assert os.path.exists(config_path)
        assert config.station.callsign == "N0CALL"

    def test_partial_config_merges_with_defaults(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")

        # Write a partial config — only station section
        partial = {"station": {"callsign": "WB4BOR"}}
        with open(config_path, "w") as f:
            yaml.dump(partial, f)

        config = load_config(config_path)

        # User value should be applied
        assert config.station.callsign == "WB4BOR"
        # Defaults should fill in the rest
        assert config.station.latitude == 0.0
        assert config.server.port == 8080
        assert config.storage.retention_days == 7

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
        assert data["station"]["callsign"] == "N0CALL"


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

        update_config(
            config, {"station": {"callsign": "WB4BOR"}}, config_path
        )

        reloaded = load_config(config_path)
        assert reloaded.station.callsign == "WB4BOR"

    def test_update_no_change(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config, {"station": {"callsign": "N0CALL"}}, config_path
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
                "station": {"callsign": "WB4BOR", "latitude": 37.75},
                "storage": {"retention_days": 30},
            },
            config_path,
        )

        assert new_config.station.callsign == "WB4BOR"
        assert new_config.station.latitude == 37.75
        assert new_config.storage.retention_days == 30
        assert len(updated) == 3
        assert restart is False
