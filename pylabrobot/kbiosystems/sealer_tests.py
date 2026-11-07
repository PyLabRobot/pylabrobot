import unittest
from typing import Dict, Optional, cast
from unittest.mock import AsyncMock, NonCallableMagicMock, call, patch

from pylabrobot.io.testing import fake_serial
from pylabrobot.kbiosystems import (
  KBiosystemsError,
  KBiosystemsUltrasealEPRO,
  KBiosystemsUltrasealPRO,
  KBiosystemsUltrasealXTPro,
  UltrasealEPROStatus,
  UltrasealPROStatus,
  UltrasealXTProStatus,
)


def sealer_io(responses: Dict[str, str]) -> NonCallableMagicMock:
  """Create a serial transport that echoes commands before their configured replies."""

  def reply(data: bytes) -> Optional[bytes]:
    """Return an echoed ASCII reply, or no response for an unmapped command."""
    command = data.decode("ascii").rstrip("\r")
    if command in responses:
      return (command + responses[command] + "\r").encode("ascii")
    return None

  return fake_serial(on_write=reply)


class KBiosystemsSealerTestBase(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Skip the firmware's real settle/setpoint sleeps so tests run instantly.
    patcher = patch("pylabrobot.kbiosystems.sealer.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)


class TestUltrasealEPRO(KBiosystemsSealerTestBase):
  def _make(self, responses: Dict[str, str]) -> KBiosystemsUltrasealEPRO:
    """Create an ePRO with configured serial replies."""
    sealer = KBiosystemsUltrasealEPRO(port="FAKE")
    sealer.io = sealer_io(responses)  # type: ignore[assignment]
    return sealer

  async def test_setup_sequence(self):
    sealer = self._make({"I": "ok", "?": "00", "V": "1.2\rboardA", "A100": "ok"})
    await sealer.setup()
    self.assertEqual(sealer.firmware_version, "1.2 | boardA")
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(
      write.await_args_list, [call(b"I\r"), call(b"?\r"), call(b"V\r"), call(b"A100\r")]
    )
    self.assertEqual(write.call_count, write.await_count)

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
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(
      write.await_args_list,
      [
        call(b"ECO_OFF\r"),
        call(b"?\r"),
        call(b"B25\r"),
        call(b"A170\r"),
        call(b"L=120\r"),
        call(b"FS=0\r"),
        call(b"DO=25\r"),
        call(b"?\r"),
        call(b"S\r"),
        call(b"?\r"),
        call(b"A100\r"),
      ],
    )
    self.assertEqual(write.call_count, write.await_count)

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
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(
      write.await_args_list,
      [
        call(b"ECO_OFF\r"),
        call(b"?\r"),
        call(b"B25\r"),
        call(b"A170\r"),
        call(b"L=120\r"),
        call(b"FS=1\r"),
        call(b"PS=30\r"),
        call(b"?\r"),
        call(b"S\r"),
        call(b"?\r"),
        call(b"A100\r"),
      ],
    )
    self.assertEqual(write.call_count, write.await_count)

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
    """Create an XT Pro with configured serial replies."""
    sealer = KBiosystemsUltrasealXTPro(port="FAKE")
    sealer.io = sealer_io(responses)  # type: ignore[assignment]
    return sealer

  async def test_setup_sequence(self):
    sealer = self._make({"?": "00", "A100": "ok"})
    await sealer.setup()
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(write.await_args_list, [call(b"?\r"), call(b"A100\r")])
    self.assertEqual(write.call_count, write.await_count)

  async def test_seal_wire_bytes(self):
    sealer = self._make({"?": "00", "B30": "ok", "A180": "ok", "A100": "ok", "S": "ok"})
    await sealer.seal(temperature=180, duration=3.0)
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(
      write.await_args_list,
      [
        call(b"?\r"),
        call(b"B30\r"),
        call(b"A180\r"),
        call(b"?\r"),
        call(b"S\r"),
        call(b"?\r"),
        call(b"A100\r"),
      ],
    )
    self.assertEqual(write.call_count, write.await_count)

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


class TestUltrasealPRO(KBiosystemsSealerTestBase):
  def _make(self, responses: Dict[str, str]) -> KBiosystemsUltrasealPRO:
    """Create a PRO with configured serial replies."""
    sealer = KBiosystemsUltrasealPRO(port="FAKE")
    sealer.io = sealer_io(responses)  # type: ignore[assignment]
    return sealer

  async def test_setup_sequence(self):
    sealer = self._make({"?": "00", "A100": "ok"})
    await sealer.setup()
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(write.await_args_list, [call(b"?\r"), call(b"A100\r")])
    self.assertEqual(write.call_count, write.await_count)

  async def test_seal_wire_bytes(self):
    sealer = self._make({"?": "00", "B30": "ok", "A180": "ok", "A100": "ok", "S": "ok"})
    await sealer.seal(temperature=180, duration=3.0)
    write = cast(AsyncMock, sealer.io.write)
    self.assertEqual(
      write.await_args_list,
      [
        call(b"?\r"),
        call(b"B30\r"),
        call(b"A180\r"),
        call(b"?\r"),
        call(b"S\r"),
        call(b"?\r"),
        call(b"A100\r"),
      ],
    )
    self.assertEqual(write.call_count, write.await_count)

  async def test_park_mode_is_bit_7(self):
    # The Ultraseal PRO reports Park Mode at 0x80 (bit 6 is spare) - unlike the
    # XT Pro, where Park Mode is 0x40. Decoding either byte must not raise.
    sealer = self._make({"?": "80"})
    self.assertEqual(await sealer.request_status(), UltrasealPROStatus.ParkMode)
    sealer = self._make({"?": "40"})
    self.assertEqual(await sealer.request_status(), UltrasealPROStatus.Spare)

  async def test_status_decode_lowair(self):
    sealer = self._make({"?": "24"})
    status = await sealer.request_status()
    self.assertEqual(status, UltrasealPROStatus.LowAir | UltrasealPROStatus.Busy)

  async def test_error_status_raises_with_code(self):
    sealer = self._make({"?": "02", "E": "09"})
    with self.assertRaises(KBiosystemsError) as ctx:
      await sealer.wait_for_idle()
    self.assertEqual(ctx.exception.error_code, 9)
    self.assertEqual(ctx.exception.message, "The sealer is overheating.")


if __name__ == "__main__":
  unittest.main()
