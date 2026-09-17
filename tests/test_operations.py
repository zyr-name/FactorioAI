"""Recovery guarantees, tested on real temporary files without Docker."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("manage", Path(__file__).resolve().parents[1] / "server/manage.py")
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(manage.SERVER / "defaults", self.root / "defaults")
        shutil.copyfile(manage.SERVER / ".env.example", self.root / ".env.example")
        for name, value in {"SERVER": self.root, "DATA": self.root / "data",
                            "BACKUPS": self.root / "backups", "ENV_FILE": self.root / ".env"}.items():
            patcher = patch.object(manage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in {
            "config": {"image": "factoriotools/factorio:2.0.77", "environment": {"DLC_SPACE_AGE": "false"}},
            "runtime_version": ("factoriotools/factorio:2.0.77", "false"),
            "active": False,
        }.items():
            patcher = patch.object(manage, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("sys.stdout", new_callable=io.StringIO)
        patcher.start()
        self.addCleanup(patcher.stop)
        manage.setup()
        self.world = manage.DATA / "saves" / "old.zip"
        self.world.write_bytes(b"original world")

    def test_setup_keeps_passwords_and_custom_settings(self):
        settings = manage.DATA / "config/server-settings.json"
        first = settings.read_bytes()
        manage.setup()
        self.assertEqual(settings.read_bytes(), first)
        self.assertTrue(json.loads(first)["game_password"])

    def test_reset_and_restore_round_trip(self):
        (manage.DATA / "mods/example.zip").write_bytes(b"mod")
        manage.reset(12345)
        self.assertFalse(self.world.exists())
        self.assertEqual(json.loads((manage.DATA / "config/map-gen-settings.json").read_text())["seed"], 12345)
        self.assertEqual((manage.DATA / "mods/example.zip").read_bytes(), b"mod")
        original_backup = next(manage.BACKUPS.glob("*.tar.gz"))
        manage.restore(original_backup)
        self.assertEqual(self.world.read_bytes(), b"original world")
        self.assertEqual(len(list(manage.BACKUPS.glob("*.tar.gz"))), 2)

    def test_failed_backup_prevents_reset(self):
        with patch.object(manage, "snapshot", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                manage.reset(None)
        self.assertEqual(self.world.read_bytes(), b"original world")

    def test_directory_swap_rolls_back_on_failure(self):
        staging = self.root / "staging"
        staging.mkdir()
        with self.assertRaises(FileNotFoundError):
            manage.replace_directory(manage.DATA / "saves", staging / "missing", staging)
        self.assertEqual(self.world.read_bytes(), b"original world")

    def test_restarts_running_server_even_if_operation_fails(self):
        with patch.object(manage, "active", return_value=True), \
                patch.object(manage, "compose") as compose, \
                patch.object(manage, "start") as start:
            with self.assertRaisesRegex(OSError, "disk full"):
                with manage.stopped():
                    raise OSError("disk full")
            compose.assert_called_once_with("stop", "factorio")
            start.assert_called_once_with()

    def test_stopped_server_stays_stopped(self):
        with patch.object(manage, "compose") as compose, patch.object(manage, "start") as start:
            with manage.stopped():
                pass
            compose.assert_not_called()
            start.assert_not_called()

    def test_invalid_archive_never_stops_server(self):
        bad = self.root / "bad.tar.gz"
        bad.write_bytes(b"broken archive")
        with patch.object(manage, "active") as active:
            with self.assertRaises(tarfile.TarError):
                manage.restore(bad)
            active.assert_not_called()
        self.assertEqual(self.world.read_bytes(), b"original world")

    def test_rejects_traversal_symlinks_and_devices(self):
        for kind, name in [(tarfile.REGTYPE, "data/../../escaped"),
                           (tarfile.SYMTYPE, "data/link"),
                           (tarfile.CHRTYPE, "data/device")]:
            with self.subTest(kind=kind):
                archive_path = self.root / "unsafe.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    archive.addfile(info)
                with patch.object(manage, "active") as active:
                    with self.assertRaisesRegex(ValueError, "Unsafe"):
                        manage.restore(archive_path)
                    active.assert_not_called()
        self.assertEqual(self.world.read_bytes(), b"original world")

    def test_wrong_version_rejected_before_downtime(self):
        backup = manage.snapshot("test")
        with patch.object(manage, "config", return_value={"image": "different", "environment": {"DLC_SPACE_AGE": "false"}}), \
                patch.object(manage, "active") as active:
            with self.assertRaisesRegex(ValueError, "Backup requires image"):
                manage.restore(backup)
            active.assert_not_called()

    def test_import_clears_newer_autosaves_but_preserves_config(self):
        imported = self.root / "import.zip"
        with zipfile.ZipFile(imported, "w") as archive:
            archive.writestr("world/level.dat", b"new world")
        (manage.DATA / "saves/_autosave99.zip").write_bytes(b"newer")
        password = (manage.DATA / "config/server-settings.json").read_bytes()
        manage.load_save(imported)
        self.assertEqual([p.name for p in (manage.DATA / "saves").iterdir()], ["world.zip"])
        self.assertEqual((manage.DATA / "config/server-settings.json").read_bytes(), password)

    def test_snapshot_excludes_scratch_data(self):
        (manage.DATA / "temp").mkdir()
        (manage.DATA / "temp/scratch").write_bytes(b"unnecessary")
        with tarfile.open(manage.snapshot("test")) as archive:
            self.assertFalse(any(name.startswith("data/temp") for name in archive.getnames()))

    def test_snapshot_failure_removes_partial_archive(self):
        (manage.DATA / "link").symlink_to(self.world)
        with self.assertRaisesRegex(ValueError, "link or special file"):
            manage.snapshot("test")
        self.assertEqual(list(manage.BACKUPS.iterdir()), [])

    def test_companion_install_is_repeatable_and_preserves_other_mods(self):
        mods = manage.DATA / "mods"
        (mods / "other_1.0.0.zip").write_bytes(b"other")
        (mods / "mod-list.json").write_text(json.dumps({"mods": [
            {"name": "base", "enabled": True},
            {"name": "other", "enabled": True},
        ]}))
        manage.install_companion()
        manage.install_companion()
        listing = json.loads((mods / "mod-list.json").read_text())["mods"]
        self.assertEqual([mod["name"] for mod in listing], [
            "base", "other", "factorio-ai-companion",
        ])
        self.assertEqual((mods / "other_1.0.0.zip").read_bytes(), b"other")
        self.assertEqual(len(list(mods.glob("factorio-ai-companion_*.zip"))), 1)
        settings = json.loads((manage.DATA / "config/server-settings.json").read_text())
        self.assertFalse(settings["auto_pause"])


if __name__ == "__main__":
    unittest.main()
