import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from systemd_watchdog import notify_watchdog


class SystemdWatchdogTests(unittest.TestCase):
    def test_returns_false_without_systemd_notify_socket(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(notify_watchdog())

    def test_sends_watchdog_notification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = Path(temp_dir) / "notify.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(str(socket_path))
                receiver.settimeout(1)
                with patch.dict(os.environ, {"NOTIFY_SOCKET": str(socket_path)}):
                    self.assertTrue(notify_watchdog())
                self.assertEqual(receiver.recv(64), b"WATCHDOG=1")


if __name__ == "__main__":
    unittest.main()
