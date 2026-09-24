import asyncio
import unittest
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, patch

from pylabrobot.high_res import HighResLidValet, HighResLidValetError

# Held before the tests patch asyncio.sleep, so the fake socket can still yield
# to the event loop while the driver's own sleeps are stubbed out.
_real_sleep = asyncio.sleep


class FakeLidValetSocket:
  """In-memory TCP stand-in mimicking the LidValet device server.

  On ``write(command)`` it queues the controller's reply: ``ACK! <command>``
  followed by the configured body (default ``OK!``), exactly like the server,
  which acknowledges then streams progress until a completion sentinel. A
  command listed in ``raw`` is answered verbatim instead, for malformed-reply
  tests.
  """

  def __init__(self, responses: Dict[str, str], raw: Optional[Dict[str, str]] = None) -> None:
    self.responses = responses
    self.raw = raw or {}
    self.port = 1000
    self.written: List[str] = []
    self._rx = bytearray()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    # Yield like a real socket would, so concurrent callers can interleave here.
    await _real_sleep(0)
    self.written.append(data.decode("ascii"))
    # the server is line-oriented: it acts on the command once the newline arrives
    command = data.decode("ascii").rstrip("\r\n")
    if command in self.raw:
      self._rx += self.raw[command].encode("ascii")
    else:
      body = self.responses.get(command, "OK!\r\n")
      self._rx += f"ACK! {command}\r\n{body}".encode("ascii")

  async def read(self, num_bytes: int = 4096, timeout: Optional[float] = None) -> bytes:
    await _real_sleep(0)
    if not self._rx:
      raise TimeoutError("no data")
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


class LidValetTestBase(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Skip the firmware's real retry sleeps so tests run instantly.
    patcher = patch("pylabrobot.high_res.lid_valet.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)

  def _make(
    self, responses: Dict[str, str], raw: Optional[Dict[str, str]] = None, num_nests: int = 2
  ) -> HighResLidValet:
    valet = HighResLidValet()
    # setup() normally reads this off the device; the fake has no settings file.
    valet._num_nests = num_nests
    valet.io = FakeLidValetSocket(responses, raw)  # type: ignore[assignment]
    return valet

  def _stub_nest_count(self, valet: HighResLidValet, count: int) -> None:
    """setup() reads the count from the settings file, which the fake has no copy of."""
    settings = SimpleNamespace(active_hotels=count)
    valet.request_settings = AsyncMock(return_value=settings)  # type: ignore[method-assign]


class TestHighResLidValet(LidValetTestBase):
  async def test_status_decode(self):
    for body, expected in [
      ("OPEN\r\nOK!\r\n", "open"),
      ("HAS_LID\r\nOK!\r\n", "has_lid"),
      ("BUSY\r\nOK!\r\n", "busy"),
      ("\r\nOK!\r\n", "unknown"),
    ]:
      valet = self._make({"status 1": body})
      self.assertEqual(await valet.request_state(1), expected)

  async def test_delid_wire_bytes(self):
    valet = self._make({"status 1": "OPEN\r\nOK!\r\n", "unlid 1": "OK!\r\n"})
    await valet.delid(1)
    self.assertEqual(valet.io.written, ["status 1\n", "unlid 1\n"])  # type: ignore[attr-defined]

  async def test_delid_refuses_when_cup_has_lid(self):
    valet = self._make({"status 1": "HAS_LID\r\nOK!\r\n"})
    with self.assertRaises(HighResLidValetError):
      await valet.delid(1)
    self.assertNotIn("unlid 1\n", valet.io.written)  # type: ignore[attr-defined]

  async def test_lid_wire_bytes(self):
    valet = self._make({"status 1": "HAS_LID\r\nOK!\r\n", "lid 1": "OK!\r\n"})
    await valet.lid(1)
    self.assertEqual(valet.io.written, ["status 1\n", "lid 1\n"])  # type: ignore[attr-defined]

  async def test_lid_retries_then_fails_without_lid(self):
    valet = self._make({"status 1": "OPEN\r\nOK!\r\n"})
    with self.assertRaises(HighResLidValetError):
      await valet.lid(1)
    # One status poll per attempt, no "lid" command ever sent.
    self.assertEqual(valet.io.written.count("status 1\n"), valet.lid_retries)  # type: ignore[attr-defined]
    self.assertNotIn("lid 1\n", valet.io.written)  # type: ignore[attr-defined]

  async def test_reset_wire_bytes(self):
    valet = self._make({"reset 2": "OK!\r\n"})
    await valet.reset(2)
    self.assertEqual(valet.io.written, ["reset 2\n"])  # type: ignore[attr-defined]

  async def test_error_reply_is_parsed(self):
    valet = self._make(
      {
        "status 1": "OPEN\r\nOK!\r\n",
        "unlid 1": "Delid Error: no plate present\r\nERROR!\r\n",
      }
    )
    with self.assertRaises(HighResLidValetError) as ctx:
      await valet.delid(1)
    self.assertEqual(str(ctx.exception), "Delid Error: no plate present")

  async def test_aborted_reply_raises(self):
    valet = self._make({"reset 1": "ABORTED!\r\n"})
    with self.assertRaises(HighResLidValetError):
      await valet.reset(1)

  async def test_invalid_response_without_ack(self):
    valet = self._make({}, raw={"status 1": "garbage\r\n"})
    with self.assertRaises(HighResLidValetError):
      await valet.request_state(1)

  async def test_nest_validation(self):
    valet = self._make({}, num_nests=2)
    with self.assertRaises(ValueError):
      await valet.reset(3)
    with self.assertRaises(ValueError):
      await valet.delid(0)

  async def test_setup_resets_every_nest_in_one_command(self):
    valet = self._make({"status": "hotel 1: OPEN\r\nhotel 2: OPEN\r\nOK!\r\n", "reset": "OK!\r\n"})
    self._stub_nest_count(valet, 2)
    await valet.setup()
    # One status round trip for the whole machine, one reset for every nest.
    self.assertEqual(valet.io.written, ["status\n", "reset\n"])  # type: ignore[attr-defined]

  async def test_setup_refuses_stuck_lid(self):
    valet = self._make({"status": "hotel 1: HAS_LID\r\nhotel 2: OPEN\r\nOK!\r\n"})
    self._stub_nest_count(valet, 2)
    with self.assertRaises(HighResLidValetError):
      await valet.setup()

  async def test_request_all_states(self):
    valet = self._make({"status": "hotel 1: OPEN\r\nhotel 2: ERROR (last failed)\r\nOK!\r\n"})
    self.assertEqual(await valet.request_all_states(), {1: "open", 2: "error"})

  async def test_reset_all_omits_nest(self):
    valet = self._make({"reset": "OK!\r\n"})
    await valet.reset()
    self.assertEqual(valet.io.written, ["reset\n"])  # type: ignore[attr-defined]

  async def test_nest_count_unknown_before_setup(self):
    valet = HighResLidValet()
    with self.assertRaises(RuntimeError):
      _ = valet.num_nests
    with self.assertRaises(RuntimeError):
      valet._validate_nest(1)

  async def test_save_cvm_wire_bytes(self):
    valet = self._make({"savecvm": "OK!\r\n"})
    await valet.save_cvm()
    self.assertEqual(valet.io.written, ["savecvm\n"])  # type: ignore[attr-defined]

  async def test_stop_tells_the_server_to_disconnect(self):
    valet = self._make({})
    await valet.stop()
    self.assertEqual(valet.io.written, ["disconnect\n"])  # type: ignore[attr-defined]

  async def test_introspection_wire_bytes(self):
    valet = self._make({"list": "OK!\r\n", "info all": "OK!\r\n", "help wave": "OK!\r\n"})
    await valet.list_commands()
    await valet.request_command_info(include_maintenance=True)
    await valet.request_command_help("wave")
    self.assertEqual(
      valet.io.written,  # type: ignore[attr-defined]
      ["list\n", "info all\n", "help wave\n"],
    )

  async def test_concurrent_commands_do_not_cross_replies(self):
    valet = self._make(
      {
        "status 1": "hotel 1: OPEN\r\nOK!\r\n",
        "status 2": "hotel 2: HAS_LID\r\nOK!\r\n",
      }
    )
    # Each command holds the connection until its own reply, so neither
    # coroutine can consume the other's.
    first, second = await asyncio.gather(valet.request_state(1), valet.request_state(2))
    self.assertEqual((first, second), ("open", "has_lid"))

  async def test_failed_nest_reports_error_state(self):
    valet = self._make({"status 1": "hotel 1: ERROR (last failed)\r\nOK!\r\n"})
    self.assertEqual(await valet.request_state(1), "error")

  async def test_vacuum_wire_bytes(self):
    valet = self._make({"vacon 1": "OK!\r\n", "vacoff 1": "OK!\r\n"})
    await valet.set_vacuum(1, True)
    await valet.set_vacuum(1, False)
    self.assertEqual(valet.io.written, ["vacon 1\n", "vacoff 1\n"])  # type: ignore[attr-defined]

  async def test_purge_wire_bytes(self):
    valet = self._make({"purgeon 2": "OK!\r\n", "purgeoff 2": "OK!\r\n"})
    await valet.set_purge(2, True)
    await valet.set_purge(2, False)
    self.assertEqual(valet.io.written, ["purgeon 2\n", "purgeoff 2\n"])  # type: ignore[attr-defined]

  async def test_pneumatics_validate_nest(self):
    valet = self._make({}, num_nests=2)
    with self.assertRaises(ValueError):
      await valet.set_vacuum(3, True)
    with self.assertRaises(ValueError):
      await valet.set_purge(0, True)

  async def test_home_and_wave_wire_bytes(self):
    valet = self._make({"home": "OK!\r\n", "wave 3": "OK!\r\n"})
    await valet.home()
    await valet.wave(3)
    self.assertEqual(valet.io.written, ["home\n", "wave 3\n"])  # type: ignore[attr-defined]

  async def test_home_offset_rejects_address_zero(self):
    valet = self._make({})
    # Address 0 hangs the controller's command queue, so it never reaches the wire.
    with self.assertRaises(ValueError):
      await valet.request_home_offset(0)
    self.assertEqual(valet.io.written, [])  # type: ignore[attr-defined]

  async def test_wave_rejects_zero_cycles(self):
    valet = self._make({})
    with self.assertRaises(ValueError):
      await valet.wave(0)

  async def test_diagnostics_strip_envelope(self):
    valet = self._make({"version": "Product Name: LidValet\r\nOK!\r\n"})
    self.assertEqual(await valet.request_version(), "Product Name: LidValet")

  async def test_change_setting_rejects_embedded_space(self):
    valet = self._make({})
    with self.assertRaises(ValueError):
      await valet.change_setting("SERVER_NAME", "two words")


if __name__ == "__main__":
  unittest.main()
