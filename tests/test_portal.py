"""Portal persistence and safety boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal.service import ServerService
from portal.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "portal.sqlite3")

    def test_default_player_settings_runs_and_events(self):
        player = self.store.player("ada")
        self.assertEqual(player["name"], "Ada")
        updated = self.store.update_player("ada", {
            "name": "Engineer", "model": "scripted", "auto_start": True,
            "ignored": "not stored",
        })
        self.assertEqual(updated["name"], "Engineer")
        self.assertEqual(updated["auto_start"], 1)
        run = self.store.start_run("ada")
        self.store.event(run, "status", {"connected": True})
        self.store.finish_run(run, "stopped")
        summary = self.store.player_summary("ada")
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["completed_runs"], 1)
        self.assertEqual(summary["events"][0]["payload"], {"connected": True})

    def test_unknown_player_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.store.update_player("missing", {"name": "Nobody"})


class ServerServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        (self.data / "config").mkdir(parents=True)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.env = self.root / ".env"
        self.env.write_text("FACTORIO_VERSION=2.0.77\nFACTORIO_BIND=127.0.0.1\nFACTORIO_PORT=34198\nFACTORIO_RCON_PORT=27016\nDLC_SPACE_AGE=false\n")
        self.settings = self.data / "config/server-settings.json"
        self.settings.write_text(json.dumps({"name": "Test", "game_password": "secret"}))
        patches = [
            patch("portal.service.manage.ENV_FILE", self.env),
            patch("portal.service.manage.DATA", self.data),
            patch("portal.service.manage.BACKUPS", self.backups),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.service = ServerService()

    def test_configuration_hides_and_preserves_password(self):
        self.assertNotIn("game_password", self.service.configuration()["server_settings"])
        with patch.object(self.service, "command", return_value="valid"):
            self.service.update_configuration({
                "environment": {"FACTORIO_PORT": "34200"},
                "server_settings": {"name": "Changed"},
            })
        stored = json.loads(self.settings.read_text())
        self.assertEqual(stored, {"name": "Changed", "game_password": "secret"})
        self.assertIn("FACTORIO_PORT=34200", self.env.read_text())

    def test_failed_validation_rolls_configuration_back(self):
        original_env = self.env.read_bytes()
        original_settings = self.settings.read_bytes()
        with patch.object(self.service, "command", side_effect=ValueError("invalid")):
            with self.assertRaisesRegex(ValueError, "invalid"):
                self.service.update_configuration({
                    "environment": {"FACTORIO_PORT": "34200"},
                    "server_settings": {"name": "Broken"},
                })
        self.assertEqual(self.env.read_bytes(), original_env)
        self.assertEqual(self.settings.read_bytes(), original_settings)

    def test_restore_accepts_only_a_backup_filename(self):
        backup = self.backups / "valid.tar.gz"
        backup.write_bytes(b"test")
        with self.assertRaisesRegex(ValueError, "existing backup"):
            self.service.action("restore", {"backup": "../valid.tar.gz"})
        with patch.object(self.service, "command", return_value="restored") as command:
            self.assertEqual(self.service.action("restore", {"backup": backup.name}), "restored")
            command.assert_called_once_with("restore", str(backup))


if __name__ == "__main__":
    unittest.main()
