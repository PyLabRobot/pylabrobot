import contextlib
import unittest
from typing import Iterator, List
from unittest.mock import AsyncMock, patch

from pylabrobot.azenta.fluidx import FluidXError, FluidXIntelliXcap96


class FakeXcapSerial:
  """In-memory serial stand-in for the ETX-framed decapper protocol.

  ``script`` is a list of turns, one per ``write``. Each turn is the list of
  reply lines the device emits in response to that write; every line is queued
  ETX-terminated, exactly as the firmware frames its replies.
  """

  def __init__(self, script: List[List[str]]) -> None:
    self.port = "FAKE"
    self.written: List[str] = []
    self._script = list(script)
    self._rx = bytearray()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def reset_input_buffer(self) -> None:
    self._rx.clear()

  def get_read_timeout(self) -> float:
    return 5.0

  def set_read_timeout(self, timeout: float) -> None:
    pass

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float) -> Iterator[None]:
    yield

  async def write(self, data: bytes) -> None:
    self.written.append(data.decode("ascii").rstrip("\x03"))
    lines = self._script.pop(0) if self._script else []
    for line in lines:
      self._rx += line.encode("ascii") + b"\x03"

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self._rx:
      return b""
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


class TestIntelliXcap96(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    patcher = patch("pylabrobot.azenta.fluidx.intellixcap96.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)

  def _make(self, script: List[List[str]]) -> FluidXIntelliXcap96:
    device = FluidXIntelliXcap96(port="FAKE")
    device.io = FakeXcapSerial(script)  # type: ignore[assignment]
    return device

  async def test_setup_checks_status_ok(self):
    device = self._make([["boot", "StatusOK"]])
    await device.setup()
    self.assertEqual(device.io.written, ["a"])  # type: ignore[attr-defined]

  async def test_setup_rejects_bad_status(self):
    device = self._make([["boot", "StatusError"]])
    with self.assertRaises(FluidXError):
      await device.setup()

  async def test_decap_success(self):
    device = self._make(
      [
        ["StatusOK"],  # status precheck
        ["ack"],  # h (decap start)
        ["l1", "l2", "DecapDONE"],  # status poll -> 3 lines, done on the last
      ]
    )
    await device.decap()
    self.assertEqual(device.io.written, ["a", "h", "a"])  # type: ignore[attr-defined]

  async def test_decap_blocked_when_recap_pending(self):
    device = self._make([["StatusRecap"]])
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_decap_reports_failure(self):
    device = self._make(
      [
        ["StatusOK"],
        ["ack"],
        ["l1", "l2", "DecapERROR"],
      ]
    )
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_recap_requires_ack(self):
    device = self._make(
      [
        ["StatusOK"],  # status precheck (no "Decap" pending)
        ["no-ack-here"],  # i reply lacks 0x06
      ]
    )
    with self.assertRaises(FluidXError):
      await device.recap()

  async def test_recap_success(self):
    device = self._make(
      [
        ["StatusOK"],
        ["\x06"],  # i acknowledged
        ["l1", "l2", "RecapDONE"],
      ]
    )
    await device.recap()
    self.assertEqual(device.io.written, ["a", "i", "a"])  # type: ignore[attr-defined]

  async def test_waste_success(self):
    device = self._make(
      [
        ["StatusOK"],
        ["\x06"],  # b acknowledged
        ["l1", "l2", "StoreDONE"],
      ]
    )
    await device.waste()
    self.assertEqual(device.io.written, ["a", "b", "a"])  # type: ignore[attr-defined]

  async def test_home_success(self):
    device = self._make(
      [
        ["\x06", "StatusOK"],  # a: ack then OK
        ["homing", "done"],  # Z: discard then no "Error"
      ]
    )
    await device.home()
    self.assertEqual(device.io.written, ["a", "Z"])  # type: ignore[attr-defined]

  async def test_open_tray_success(self):
    device = self._make(
      [
        ["StatusOK"],  # status precheck
        ["\x06", "OpenDONE", "StatusOK"],  # f: ack + two follow-up lines
      ]
    )
    await device.open_tray()
    self.assertEqual(device.io.written, ["a", "f"])  # type: ignore[attr-defined]

  async def test_reset_success(self):
    device = self._make(
      [
        ["StatusOK"],  # a precheck (no error)
        ["resetting"],  # z
        ["StatusOK"],  # a postcheck (no error)
      ]
    )
    await device.reset()
    self.assertEqual(device.io.written, ["a", "z", "a"])  # type: ignore[attr-defined]

  async def test_standby_and_ready(self):
    device = self._make([["StatusOK"], ["StandbyDONE"]])
    await device.standby()
    self.assertEqual(device.io.written, ["a", "j"])  # type: ignore[attr-defined]

    device = self._make([["StatusSleep"], ["ReadyDONE"]])
    await device.ready()
    self.assertEqual(device.io.written, ["a", "k"])  # type: ignore[attr-defined]


if __name__ == "__main__":
  unittest.main()
