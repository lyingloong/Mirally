"""The product launcher has one framework owner and no retired backend route."""
import sys
import unittest
from unittest.mock import patch

from mirally.__main__ import main


class FrameworkEntryTests(unittest.TestCase):
    def test_launch_delegates_to_the_owned_framework(self):
        with patch.object(sys, "platform", "win32"), \
             patch("mirally.framework_host.main", return_value=0) as framework:
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 0)
        framework.assert_called_once_with()

    def test_removed_backend_flag_is_rejected_before_creating_a_host(self):
        with patch.object(sys, "platform", "win32"), \
             patch.object(sys, "argv", ["mirally", "--openclaw"]), \
             patch("mirally.framework_host.FrameworkHost") as host, \
             patch("sys.stderr"):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)
        host.assert_not_called()

    def test_unsupported_platform_stops_before_host_creation(self):
        with patch.object(sys, "platform", "linux"), \
             patch("mirally.framework_host.FrameworkHost") as host:
            with self.assertRaises(SystemExit):
                main()
        host.assert_not_called()


if __name__ == "__main__":
    unittest.main()
