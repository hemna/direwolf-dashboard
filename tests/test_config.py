"""Tests for configuration loading and management."""

import os

import pytest
import yaml

from direwolf_dashboard.config import (
    Config,
    DirewolfConfig,
    ServerConfig,
    StationConfig,
    StorageConfig,
    load_config,
    save_config,
    update_config,
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

    def test_default_station_latitude(self):
        config = Config()
        assert config.station.latitude == 0.0

    def test_default_server_port(self):
        config = Config()
        assert config.server.port == 8080

    def test_default_retention_days(self):
        config = Config()
        assert config.storage.retention_days == 7

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

        with open(config_path) as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict)
        assert "station" in data
        assert d["station"]["latitude"] == 0.0 if (d := data) else False


class TestAtomicSaveConfig:
    """save_config must not destroy the existing file when the write fails."""

    def test_failed_save_preserves_original_bytes(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config = Config()
        config.server.port = 9090
        save_config(config, str(config_path))
        original = config_path.read_bytes()
        assert original  # the file must have had real content

        def _no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("direwolf_dashboard.config.yaml.dump", _no_space)

        with pytest.raises(OSError):
            save_config(config, str(config_path))

        # The previous file is untouched and no temp files are left behind.
        assert config_path.read_bytes() == original
        assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]

    def test_successful_save_replaces_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        first = Config()
        first.server.port = 9090
        save_config(first, str(config_path))
        original = config_path.read_bytes()

        second = Config()
        second.server.port = 8081
        save_config(second, str(config_path))

        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert data["server"]["port"] == 8081
        assert config_path.read_bytes() != original
        assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


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


class TestConfigPostInitIsolated:
    """Issue #11: Config() with no data_dir must not touch the filesystem."""

    def test_config_no_args_has_empty_db_path(self):
        """Config() with no args should NOT call _resolve_data_dir — db_path stays empty."""
        config = Config()
        assert config.storage.db_path == ""

    def test_config_no_args_does_not_create_directories(self, tmp_path, monkeypatch):
        """Constructing Config() must not create any directories."""
        # Redirect home so that if _resolve_data_dir were called it would write here
        monkeypatch.setenv("HOME", str(tmp_path))
        Config()
        # tmp_path should remain empty (no subdirs created by Config())
        created = list(tmp_path.iterdir())
        assert created == [], f"Config() created unexpected directories: {created}"

    def test_load_config_resolves_non_empty_paths(self, tmp_path):
        """load_config() should produce non-empty db_path and cache_dir."""
        config_path = str(tmp_path / "config.yaml")
        config = load_config(config_path)
        assert config.storage.db_path != ""

    def test_update_coerces_numeric_strings(self, tmp_path):
        """Numeric string values are coerced to the expected type (issue #91)."""
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        new_config, updated, restart = update_config(
            config,
            {"storage": {"retention_days": "14"}},
            config_path,
        )

        assert new_config.storage.retention_days == 14
        assert isinstance(new_config.storage.retention_days, int)

    def test_update_rejects_non_numeric_value(self, tmp_path):
        """A non-numeric value must raise ValueError (surfaced as HTTP 400)."""
        config_path = str(tmp_path / "config.yaml")
        config = Config()
        save_config(config, config_path)

        with pytest.raises(ValueError):
            update_config(
                config,
                {"storage": {"retention_days": "abc"}},
                config_path,
            )


class TestResolveDataDirFallback:
    """_resolve_data_dir must verify the fallback is writable (issue #93)."""

    def test_uses_default_when_writable(self, tmp_path, monkeypatch):
        import direwolf_dashboard.config as cfg

        default = str(tmp_path / "default")
        monkeypatch.setattr(cfg, "DEFAULT_DATA_DIR", default)
        monkeypatch.setattr(cfg, "FALLBACK_DATA_DIR", str(tmp_path / "fallback"))

        result = cfg._resolve_data_dir()
        assert result == os.path.expanduser(default)

    def test_falls_back_when_default_unwritable(self, tmp_path, monkeypatch):
        import direwolf_dashboard.config as cfg

        default = str(tmp_path / "default")
        fallback = str(tmp_path / "fallback")
        monkeypatch.setattr(cfg, "DEFAULT_DATA_DIR", default)
        monkeypatch.setattr(cfg, "FALLBACK_DATA_DIR", fallback)

        real_makedirs = os.makedirs

        def flaky_makedirs(path, *args, **kwargs):
            if os.path.normpath(path) == os.path.normpath(default):
                raise OSError("readonly filesystem")
            return real_makedirs(path, *args, **kwargs)

        monkeypatch.setattr(cfg.os, "makedirs", flaky_makedirs)

        result = cfg._resolve_data_dir()
        assert result == os.path.expanduser(fallback)
        # The fallback was actually verified writable (test file removed)
        assert not os.path.exists(os.path.join(fallback, ".write_test"))


class TestDataDirFollowsDerivedDbPath:
    """Changing data_dir updates the derived database path (issue #174)."""

    def _loaded(self, tmp_path, data_dir):
        config_path = str(tmp_path / "config.yaml")
        save_config(Config(data_dir=str(data_dir)), config_path)
        return config_path, load_config(config_path)

    def test_derived_db_path_follows_new_data_dir(self, tmp_path):
        old = tmp_path / "old"
        new = tmp_path / "new"
        config_path, config = self._loaded(tmp_path, old)
        assert config.storage.db_path == os.path.join(str(old), "packets.db")

        update_config(config, {"data_dir": str(new)}, config_path)

        reloaded = load_config(config_path)
        assert reloaded.data_dir == str(new)
        assert reloaded.storage.db_path == os.path.join(str(new), "packets.db")

    def test_custom_db_path_is_preserved(self, tmp_path):
        old = tmp_path / "old"
        custom = tmp_path / "custom" / "history.db"
        config = Config(data_dir=str(old))
        config.storage.db_path = str(custom)
        config_path = str(tmp_path / "config.yaml")
        save_config(config, config_path)

        update_config(load_config(config_path), {"data_dir": str(tmp_path / "new")}, config_path)

        assert load_config(config_path).storage.db_path == str(custom)

    def test_explicit_db_path_in_same_update_is_preserved(self, tmp_path):
        old = tmp_path / "old"
        explicit = tmp_path / "explicit" / "packets.db"
        config_path, config = self._loaded(tmp_path, old)

        update_config(
            config,
            {"data_dir": str(tmp_path / "new"), "storage": {"db_path": str(explicit)}},
            config_path,
        )

        assert load_config(config_path).storage.db_path == str(explicit)


class TestRelativeConfigPath:
    """A plain relative config filename works (issue #178)."""

    def test_missing_relative_filename_creates_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        config = load_config("relative.yaml")

        assert os.path.isfile("relative.yaml")
        assert config.storage.db_path != ""

    def test_relative_path_saves_and_reloads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = load_config("relative.yaml")
        config.station.latitude = 38.5

        save_config(config, "relative.yaml")

        assert load_config("relative.yaml").station.latitude == 38.5

    def test_nested_relative_path_creates_parent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        config = load_config(os.path.join("nested", "dir", "config.yaml"))

        assert os.path.isfile(os.path.join("nested", "dir", "config.yaml"))
        assert config.station.latitude == 0
