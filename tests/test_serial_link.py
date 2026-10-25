import threading
import unittest
from unittest.mock import patch

from pc.acrylic_pan_monitor.serial_link import SerialLink


class FailingSerial:
    def __init__(self):
        self.is_open = True
        self.in_waiting = 0

    def read(self, _size):
        raise OSError("USB device was removed")

    def close(self):
        self.is_open = False


class SerialLinkTests(unittest.TestCase):
    def test_usb_read_error_publishes_disconnected_state(self):
        failed = threading.Event()
        errors = []
        serial_port = FailingSerial()
        link = SerialLink(lambda _frame: None,
                          lambda error: (errors.append(error), failed.set()))
        with patch("pc.acrylic_pan_monitor.serial_link.serial.Serial",
                   return_value=serial_port):
            link.connect("/dev/ttyACM0")
            self.assertTrue(failed.wait(1.0))
        if link._thread is not None:
            link._thread.join(timeout=1.0)
        self.assertFalse(link.connected)
        self.assertEqual(str(errors[0]), "USB device was removed")
        link.disconnect()


if __name__ == "__main__":
    unittest.main()
