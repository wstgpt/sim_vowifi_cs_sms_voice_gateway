import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from host import mdd_orchestrator
from host.mdd_orchestrator import Orchestrator
from host.vpcd_modem_bridge import (ModemError, ModemManagerCard,
                                    allocate_logical_channels,
                                    logical_channel_metadata)


class ModemBackendTests(unittest.TestCase):
    def test_logical_channel_metadata_exposes_capacity_roles_and_ids(self):
        value = logical_channel_metadata([1, 2, 3])
        self.assertEqual(value["channel_capacity"], 3)
        self.assertEqual(value["channel_allocated"], 3)
        self.assertEqual(value["channel_status"], "ready")
        self.assertEqual(value["logical_channels"], [
            {"slot": 0, "channel": 1, "role": "pin"},
            {"slot": 1, "channel": 2, "role": "swu"},
            {"slot": 2, "channel": 3, "role": "ims"},
        ])

    def test_partial_logical_channel_allocation_is_released_with_clear_error(self):
        class Card:
            def __init__(self):
                self.values = iter((1, 1))
                self.closed = []

            def open_channel(self):
                return next(self.values)

            def close_channel(self, channel):
                self.closed.append(channel)

        card = Card()
        with self.assertRaisesRegex(ModemError,
                                    "SIM logical channel allocation failed \\(1/3 allocated\\)"):
            allocate_logical_channels(card, 3)
        self.assertEqual(card.closed, [1])

    def test_modemmanager_command_backend(self):
        card = ModemManagerCard.__new__(ModemManagerCard)
        card.lock = threading.RLock()
        card.timeout = 10
        card.modem = "0"
        card.debug = False
        result = SimpleNamespace(returncode=0, stdout=b'response: \'+CSIM: 4,"9000"\'\n', stderr=b"")
        with patch("host.vpcd_modem_bridge.subprocess.run", return_value=result) as invoke:
            self.assertEqual(card.csim(bytes.fromhex("00A40000023F00")), bytes.fromhex("9000"))
        self.assertTrue(any(value.startswith("--command=AT+CSIM=14,")
                            for value in invoke.call_args.args[0]))

    def test_modemmanager_tty_mapping(self):
        def fake_run(args, **_kwargs):
            if args == ["mmcli", "-L"]:
                return SimpleNamespace(returncode=0,
                    stdout="/org/freedesktop/ModemManager1/Modem/2\n")
            return SimpleNamespace(returncode=0,
                stdout="modem.generic.ports.value[1] : ttyUSB2 (at)\n")
        with patch.object(mdd_orchestrator, "run", side_effect=fake_run):
            self.assertEqual(Orchestrator.modemmanager_modem_for_tty("/dev/ttyUSB2"),
                             "/org/freedesktop/ModemManager1/Modem/2")

if __name__ == "__main__":
    unittest.main()
