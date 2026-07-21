import contextlib
import unittest
from typing import Iterator, List
from unittest.mock import AsyncMock, patch

from pylabrobot.azenta.fluidx import FluidXError, FluidXIntelliXcap96

ACK = "\x06"


class FakeXcapSerial:
  """In-memory serial stand-in for the STX/ETX framed decapper protocol.

  ``script`` is a list of turns, one per ``write``. Each turn is the list of
  frame payloads the device emits in response to that write; every payload is
  queued wrapped in STX (0x02) .. ETX (0x03), exactly as the firmware frames it.
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
    turn = self._script.pop(0) if self._script else []
    for payload in turn:
      self._rx += b"\x02" + payload.encode("ascii") + b"\x03"

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self._rx:
      return b""
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


def status(word: str, echo: str = "a") -> List[str]:
  """A full status reply: ACK, command echo, and the status word."""
  return [ACK, f"{echo}OK", word]


class TestIntelliXcap96(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    patcher = patch("pylabrobot.azenta.fluidx.intellixcap96.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)

  def _make(self, script: List[List[str]]) -> FluidXIntelliXcap96:
    device = FluidXIntelliXcap96(port="FAKE")
    device.io = FakeXcapSerial(script)  # type: ignore[assignment]
    return device

  async def test_setup_ok(self):
    device = self._make([status("StatusOK")])
    await device.setup()
    self.assertEqual(device.io.written, ["a"])  # type: ignore[attr-defined]

  async def test_setup_busy_raises_estop_hint(self):
    device = self._make([status("StatusBUSY")])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertIn("E-STOP", str(ctx.exception))

  async def test_setup_error_raises(self):
    device = self._make([status("StatusError")])
    with self.assertRaises(FluidXError):
      await device.setup()

  async def test_request_status_returns_status_word(self):
    device = self._make([status("StatusOK")])
    self.assertEqual(await device.request_status(), "StatusOK")

  async def test_open_tray_moves_then_idle(self):
    device = self._make(
      [
        [ACK, "fOK"],  # f accepted
        status("StatusBUSY"),  # still moving
        status("StatusOK"),  # settled
      ]
    )
    await device.open_tray()
    self.assertEqual(device.io.written, ["f", "a", "a"])  # type: ignore[attr-defined]

  async def test_open_tray_ignored_raises(self):
    device = self._make([[ACK, "fOK", "CommandIgnore"]])
    with self.assertRaises(FluidXError):
      await device.open_tray()

  async def test_close_tray_moves_then_idle(self):
    device = self._make([[ACK, "gOK"], status("StatusBUSY"), status("StatusOK")])
    await device.close_tray()
    self.assertEqual(device.io.written, ["g", "a", "a"])  # type: ignore[attr-defined]

  async def test_home_moves_then_idle(self):
    device = self._make([[ACK, "ZOK"], status("StatusBUSY"), status("StatusOK")])
    await device.home()
    self.assertEqual(device.io.written, ["Z", "a", "a"])  # type: ignore[attr-defined]

  async def test_standby_reaches_sleep(self):
    device = self._make([[ACK, "jOK"], status("StatusSLEEP")])
    await device.standby()
    self.assertEqual(device.io.written, ["j", "a"])  # type: ignore[attr-defined]

  async def test_ready_noop_when_awake(self):
    device = self._make([status("StatusOK")])
    await device.ready()
    self.assertEqual(device.io.written, ["a"])  # type: ignore[attr-defined]

  async def test_ready_wakes_from_sleep(self):
    device = self._make(
      [
        status("StatusSLEEP"),  # request_status: asleep
        [ACK, "kOK"],  # k accepted
        status("StatusBUSY"),  # waking
        status("StatusOK"),  # awake
      ]
    )
    await device.ready()
    self.assertEqual(device.io.written, ["a", "k", "a", "a"])  # type: ignore[attr-defined]

  async def test_decap_success(self):
    device = self._make(
      [
        status("StatusOK"),  # precheck: not recapped, no error
        [ACK, "hOK"],  # h accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.decap()
    self.assertEqual(device.io.written, ["a", "h", "a", "a"])  # type: ignore[attr-defined]

  async def test_decap_blocked_when_already_decapped(self):
    device = self._make([status("StatusRecap")])
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_decap_reports_error_during_motion(self):
    device = self._make([status("StatusOK"), [ACK, "hOK"], [ACK, "aOK", "DecapERROR"]])
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_recap_blocked_when_not_decapped(self):
    device = self._make([status("StatusDecap")])
    with self.assertRaises(FluidXError):
      await device.recap()

  async def test_reset_ignored_is_not_fatal(self):
    device = self._make([[ACK, "zOK", "CommandIgnore"], status("StatusOK")])
    await device.reset()
    self.assertEqual(device.io.written, ["z", "a"])  # type: ignore[attr-defined]


if __name__ == "__main__":
  unittest.main()
