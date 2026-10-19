import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from start_kody import (
    ConfigurationError,
    LauncherLock,
    create_log_handler,
    load_runtime_configuration,
)


class RuntimeConfigurationTests(unittest.TestCase):
    def write_configuration(self, runtime: dict[str, object]) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "kody.json"
        path.write_text(json.dumps({"runtime": runtime}), encoding="utf-8")
        return path

    def test_loads_enabled_services_and_runtime_options(self) -> None:
        path = self.write_configuration(
            {
                "host": "127.0.0.1",
                "logRetentionMinutes": 30,
                "services": [
                    {
                        "id": "kody-web",
                        "displayName": "Kody web application",
                        "module": "kody_app.app:app",
                        "workingDirectory": "products/kody",
                        "port": 8000,
                        "enabled": True,
                    },
                    {
                        "id": "disabled",
                        "displayName": "Disabled service",
                        "module": "example.app:app",
                        "workingDirectory": "products/kody",
                        "port": 8100,
                        "enabled": False,
                    },
                ],
            }
        )

        host, retention_minutes, services = load_runtime_configuration(path)

        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(retention_minutes, 30)
        self.assertEqual([(service.id, service.port) for service in services], [("kody-web", 8000)])

    def test_rejects_duplicate_enabled_ports(self) -> None:
        path = self.write_configuration(
            {
                "host": "127.0.0.1",
                "logRetentionMinutes": 30,
                "services": [
                    {
                        "id": "first",
                        "displayName": "First",
                        "module": "first.app:app",
                        "workingDirectory": "products/kody",
                        "port": 8000,
                    },
                    {
                        "id": "second",
                        "displayName": "Second",
                        "module": "second.app:app",
                        "workingDirectory": "products/kody",
                        "port": 8000,
                    },
                ],
            }
        )

        with self.assertRaisesRegex(ConfigurationError, "port is duplicated"):
            load_runtime_configuration(path)

    def test_creates_one_minute_rolling_log_with_thirty_file_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("start_kody.LOG_DIRECTORY", Path(temporary_directory)):
                handler = create_log_handler("shapes", 30)
            try:
                self.assertEqual(handler.interval, 60)
                self.assertEqual(handler.backupCount, 30)
                self.assertEqual(handler.baseFilename, str(Path(temporary_directory) / "shapes" / "service.log"))
            finally:
                handler.close()

    def test_prevents_a_second_launcher_from_acquiring_the_same_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock_path = Path(temporary_directory) / "start_kody.lock"
            first_launcher = LauncherLock(lock_path)
            first_launcher.acquire()
            self.addCleanup(first_launcher.release)

            with self.assertRaisesRegex(ConfigurationError, "already running"):
                LauncherLock(lock_path).acquire()


if __name__ == "__main__":
    unittest.main()
