import unittest
from typing import Dict, List
from unittest.mock import AsyncMock, patch

from pylabrobot.kbiosystems import (
  KBiosystemsError,
  KBiosystemsSealer,
  KBiosystemsUltrasealEPRO,
  KBiosystemsUltrasealXTPro,
  UltrasealEPROStatus,
  UltrasealXTProStatus,
)


class FakeSealerSerial:
  """In-memory serial stand-in that mimics the sealer's echo protocol.

  On ``write(command + "\\r")`` it records the command and, if a reply body is
  configured for it, queues ``command + body + "\\r"`` to be read back - exactly
  like the device, which echoes the command in front of its reply.
  """

  def __init__(self, responses: Dict[str, str]) -> None:
    self.responses = responses
    self.port = "FAKE"
    self.written: List[str] = []
    self._rx = bytearray()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def reset_input_buffer(self) -> None:
    self._rx.clear()

  async def write(self, data: bytes) -> None:
    command = data.decode("ascii").rstrip("\r")
    self.written.append(command)
    if command in self.responses:
      self._rx += (command + self.responses[command] + "\r").encode("ascii")

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self._rx:
      return b""
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


class KBiosystemsSealerTestBase(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Skip the firmware's real settle/setpoint sleeps so tests run instantly.
    patcher = patch("pylabrobot.kbiosystems.sealer.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)


class TestUltrasealEPRO(KBiosystemsSealerTestBase):
  def _make(self, responses: Dict[str, str]) -> KBiosystemsUltrasealEPRO:
    sealer = KBiosystemsUltrasealEPRO(port="FAKE")
    sealer.io = FakeSealerSerial(responses)  # type: ignore[assignment]
    return sealer

  async def test_setup_sequence(self):
    sealer = self._make({"I": "ok", "?": "00", "V": "1.2\rboardA", "A100": "ok"})
    await sealer.setup()
    self.assertEqual(sealer.firmware_version, "1.2 | boardA")
    self.assertIn("I", sealer.io.written)  # type: ignore[attr-defined]
    self.assertIn("V", sealer.io.written)  # type: ignore[attr-defined]
    self.assertIn("A100", sealer.io.written)  # type: ignore[attr-defined]

  async def test_seal_distance_mode_wire_bytes(self):
    sealer = self._make(
      {
        "ECO_OFF": "ok",
        "?": "00",
        "B25": "ok",
        "A170": "ok",
        "A100": "ok",
        "L=120": "ok",
        "FS=0": "ok",
        "DO=25": "ok",
        "S": "ok",
      }
    )
    await sealer.seal(temperature=170, duration=2.5)
    written = sealer.io.written  # type: ignore[attr-defined]
    for expected in ["ECO_OFF", "B25", "A170", "L=120", "FS=0", "DO=25", "S", "A100"]:
      self.assertIn(expected, written)
    # Distance mode must not touch the force commands.
    self.assertFalse(any(w.startswith("PS=") or w == "FS=1" for w in written))

  async def test_seal_force_mode_wire_bytes(self):
    sealer = self._make(
      {
        "ECO_OFF": "ok",
        "?": "00",
        "B25": "ok",
        "A170": "ok",
        "A100": "ok",
        "L=120": "ok",
        "FS=1": "ok",
        "S": "ok",
      }
    )
    await sealer.seal(temperature=170, duration=2.5, force_mode=True, sealing_force=30)
    written = sealer.io.written  # type: ignore[attr-defined]
    self.assertIn("FS=1", written)
    self.assertIn("PS=30", written)  # sent with no reply
    self.assertFalse(any(w.startswith("DO=") for w in written))

  async def test_status_decode(self):
    sealer = self._make({"?": "a4"})
    status = await sealer.request_status()
    self.assertEqual(
      status,
      UltrasealEPROStatus.ParkMode | UltrasealEPROStatus.NotInitialised | UltrasealEPROStatus.Busy,
    )

  async def test_temperature_floor_is_5(self):
    sealer = self._make({"A005": "ok"})
    await sealer.set_temperature(5)  # allowed on the ePRO
    with self.assertRaises(ValueError):
      await sealer.set_temperature(4)


class TestUltrasealXTPro(KBiosystemsSealerTestBase):
  def _make(self, responses: Dict[str, str]) -> KBiosystemsUltrasealXTPro:
    sealer = KBiosystemsUltrasealXTPro(port="FAKE")
    sealer.io = FakeSealerSerial(responses)  # type: ignore[assignment]
    return sealer

  async def test_setup_sequence(self):
    sealer = self._make({"?": "00", "A100": "ok"})
    await sealer.setup()
    self.assertEqual(sealer.io.written, ["?", "A100"])  # type: ignore[attr-defined]

  async def test_seal_wire_bytes(self):
    sealer = self._make({"?": "00", "B30": "ok", "A180": "ok", "A100": "ok", "S": "ok"})
    await sealer.seal(temperature=180, duration=3.0)
    written = sealer.io.written  # type: ignore[attr-defined]
    for expected in ["B30", "A180", "S", "A100"]:
      self.assertIn(expected, written)
    # The XT Pro has no foil/force/distance/eco commands.
    self.assertFalse(any(w.startswith(("L=", "DO=", "PS=", "FS=", "ECO_")) for w in written))

  async def test_shuttle_commands(self):
    sealer = self._make({"P": "ok", "U": "ok", "R": "ok"})
    self.assertTrue(await sealer.park())
    self.assertTrue(await sealer.unpark())
    self.assertTrue(await sealer.reset())

  async def test_status_decode_lowair(self):
    sealer = self._make({"?": "24"})
    status = await sealer.request_status()
    self.assertEqual(status, UltrasealXTProStatus.LowAir | UltrasealXTProStatus.Busy)

  async def test_temperature_floor_is_25(self):
    sealer = self._make({"A025": "ok"})
    await sealer.set_temperature(25)
    with self.assertRaises(ValueError):
      await sealer.set_temperature(20)  # allowed on the ePRO, not the XT Pro

  async def test_error_status_raises_with_code(self):
    sealer = self._make({"?": "02", "E": "12"})
    with self.assertRaises(KBiosystemsError) as ctx:
      await sealer.wait_for_idle()
    self.assertEqual(ctx.exception.error_code, 12)
    self.assertEqual(ctx.exception.message, "Low air pressure.")


class TestSharedBase(unittest.TestCase):
  def test_both_drivers_subclass_base(self):
    self.assertTrue(issubclass(KBiosystemsUltrasealEPRO, KBiosystemsSealer))
    self.assertTrue(issubclass(KBiosystemsUltrasealXTPro, KBiosystemsSealer))

  def test_base_is_abstract(self):
    with self.assertRaises(TypeError):
      KBiosystemsSealer(port="FAKE")  # setup/seal are abstract

  def test_shared_low_status_bits(self):
    # The state machine in the base relies on these being identical.
    for bit in ("Ready", "NoFoil", "Error", "Busy", "NotAtSealTemperature", "PlateNotPresent"):
      self.assertEqual(
        int(getattr(UltrasealEPROStatus, bit)), int(getattr(UltrasealXTProStatus, bit))
      )


if __name__ == "__main__":
  unittest.main()
