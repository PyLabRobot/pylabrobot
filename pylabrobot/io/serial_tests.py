import asyncio
import types
import unittest
from unittest.mock import patch

from pylabrobot.io.serial import HAS_SERIAL, find_serial_ports


class TestSerialDiscovery(unittest.TestCase):
  def test_find_serial_ports_matches_multiple_vid_pid_pairs(self) -> None:
    ports = [
      types.SimpleNamespace(device="/dev/tty-one", vid=0x1234, pid=0x0001),
      types.SimpleNamespace(device="/dev/tty-two", vid=0x1234, pid=0x0002),
      types.SimpleNamespace(device="/dev/tty-other", vid=0x9999, pid=0x0001),
    ]
    serial_module = types.SimpleNamespace(
      tools=types.SimpleNamespace(
        list_ports=types.SimpleNamespace(comports=lambda: ports),
      )
    )

    with (
      patch("pylabrobot.io.serial.HAS_SERIAL", True),
      patch("pylabrobot.io.serial.serial", serial_module, create=True),
    ):
      discovered = find_serial_ports({(0x1234, 0x0001), (0x1234, 0x0002)})

    self.assertEqual(discovered, ["/dev/tty-one", "/dev/tty-two"])

  def test_find_serial_ports_reports_missing_pyserial(self) -> None:
    with patch("pylabrobot.io.serial.HAS_SERIAL", False):
      with self.assertRaisesRegex(RuntimeError, "pyserial is not installed"):
        find_serial_ports({(0x1234, 0x0001)})


class TestSerialNumber(unittest.TestCase):
  def test_matches_with_windows_letter_either_way(self) -> None:
    from pylabrobot.io.serial import serial_number_matches

    self.assertTrue(serial_number_matches("D30G5GV5", "D30G5GV5"))
    self.assertTrue(serial_number_matches("D30G5GV5A", "D30G5GV5"))  # Windows' FTDI driver
    self.assertTrue(serial_number_matches("D30G5GV5", "D30G5GV5A"))  # copied from Windows
    self.assertTrue(serial_number_matches("d30g5gv5a", "D30G5GV5"))
    self.assertFalse(serial_number_matches("D30G5GV51", "D30G5GV5"))  # a digit isn't the letter
    self.assertFalse(serial_number_matches("D30G5GV5E", "D30G5GV5"))  # interfaces are A to D
    self.assertFalse(serial_number_matches("D30G5GV6", "D30G5GV5"))
    self.assertFalse(serial_number_matches(None, "D30G5GV5"))

  def test_find_serial_ports_by_serial_number(self) -> None:
    ports = [
      types.SimpleNamespace(device="COM4", vid=0x0403, pid=0x6015, serial_number="AAAA1111A"),
      types.SimpleNamespace(device="COM5", vid=0x0403, pid=0x6015, serial_number="BBBB2222A"),
    ]
    serial_module = types.SimpleNamespace(
      tools=types.SimpleNamespace(list_ports=types.SimpleNamespace(comports=lambda: ports))
    )
    with (
      patch("pylabrobot.io.serial.HAS_SERIAL", True),
      patch("pylabrobot.io.serial.serial", serial_module, create=True),
    ):
      self.assertEqual(find_serial_ports({(0x0403, 0x6015)}, serial_number="BBBB2222"), ["COM5"])
      self.assertEqual(find_serial_ports({(0x0403, 0x6015)}, serial_number="CCCC"), [])


class FakePort:
  """A stand-in for serial.Serial, opened by the constructor or by open()."""

  def __init__(self, port=None, **kwargs):
    self.port, self.dtr, self.rts, self.is_open = port, True, True, False
    self.opened_with = None
    self.written = bytearray()
    self.incoming = bytearray()
    self.timeout = kwargs.get("timeout", 1)
    if port is not None:
      self.open()

  def open(self):
    self.is_open = True
    self.opened_with = (self.dtr, self.rts)

  def close(self):
    self.is_open = False

  def cancel_read(self):
    pass

  def write(self, data):
    self.written += data
    return len(data)

  def read(self, size=1):
    out, self.incoming = bytes(self.incoming[:size]), self.incoming[size:]
    return out


@unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
class TestSerialOpenAndBinary(unittest.IsolatedAsyncioTestCase):
  def _serial(self, **kwargs):
    from pylabrobot.io.serial import Serial

    return Serial("test device", **kwargs)

  async def test_finds_the_port_by_serial_number(self) -> None:
    ports = [
      types.SimpleNamespace(device="COM4", hwid="USB VID:PID=0403:6015", serial_number="AAAA1111A"),
      types.SimpleNamespace(device="COM5", hwid="USB VID:PID=0403:6015", serial_number="BBBB2222A"),
    ]
    with (
      patch("serial.tools.list_ports.comports", lambda: ports),
      patch("serial.Serial", FakePort),
    ):
      io = self._serial(vid=0x0403, pid=0x6015, serial_number="BBBB2222")
      await io.setup()
      self.assertEqual(io.port, "COM5")
      await io.stop()
      io = self._serial(vid=0x0403, pid=0x6015, serial_number="CCCC")
      with self.assertRaisesRegex(RuntimeError, r"seen: COM4 \(AAAA1111A\), COM5"):
        await io.setup()

  async def test_dtr_and_rts_set_before_the_port_opens(self) -> None:
    with patch("serial.Serial", FakePort):
      io = self._serial(port="COM9", dtr=False, rts=False)
      await io.setup()
      assert io._ser is not None
      self.assertEqual(io._ser.opened_with, (False, False))
      await io.stop()
      io = self._serial(port="COM9")  # neither given: pyserial's defaults
      await io.setup()
      assert io._ser is not None
      self.assertEqual(io._ser.opened_with, (True, True))
      await io.stop()

  async def test_binary_data_with_a_trailing_backslash(self) -> None:
    # A trailing backslash is binary data, not an escape: it is logged and returned as it came.
    with patch("serial.Serial", FakePort):
      io = self._serial(port="COM9")
      await io.setup()
      assert io._ser is not None
      await io.write(b"\xaa\x01\\")
      self.assertEqual(bytes(io._ser.written), b"\xaa\x01\\")
      io._ser.incoming += b"\xcc\x02\\"
      self.assertEqual(await io.read(3), b"\xcc\x02\\")
      await io.stop()

  async def test_write_while_reading(self) -> None:
    # A write isn't queued behind a read that waits for bytes.
    import threading

    class SlowRead(FakePort):
      def read(self, size=1):
        threading.Event().wait(1.0)  # a read waiting for bytes until its timeout
        return b""

    with patch("serial.Serial", SlowRead):
      for together, slower_than in ((True, None), (False, 0.7)):
        io = self._serial(port="COM9", write_while_reading=together)
        await io.setup()
        read = asyncio.ensure_future(io.read(1))
        await asyncio.sleep(0.05)
        t0 = asyncio.get_running_loop().time()
        await io.write(b"\x01")
        took = asyncio.get_running_loop().time() - t0
        if slower_than is None:
          self.assertLess(took, 0.5)
        else:
          self.assertGreater(took, slower_than)
        await read
        await io.stop()

  async def test_stop_with_a_read_waiting(self) -> None:
    import threading

    class WaitingRead(FakePort):
      def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancelled = threading.Event()

      def read(self, size=1):
        self.cancelled.wait(5.0)  # a read waiting for bytes
        return b""

      def cancel_read(self):
        self.cancelled.set()

    with patch("serial.Serial", WaitingRead):
      io = self._serial(port="COM9")
      await io.setup()
      read = asyncio.ensure_future(io.read(1))
      await asyncio.sleep(0.05)
      t0 = asyncio.get_running_loop().time()
      await io.stop()
      self.assertLess(asyncio.get_running_loop().time() - t0, 2.0)
      self.assertEqual(await read, b"")
      self.assertFalse(io.is_open)


class TestValidatorReadAvailable(unittest.IsolatedAsyncioTestCase):
  async def test_replays_a_read(self) -> None:
    from pylabrobot.io.serial import SerialValidator

    class Reader:
      def next_command(self):
        return {"module": "serial", "device_id": "COM9", "action": "read", "data": "ab"}

    io = SerialValidator(Reader(), "test device", port="COM9")  # type: ignore[arg-type]
    self.assertEqual(await io.read_available(), b"ab")
