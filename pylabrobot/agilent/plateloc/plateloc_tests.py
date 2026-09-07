import asyncio
import contextlib
import unittest
from collections import deque
from typing import Deque
from unittest.mock import MagicMock, patch

import pylabrobot.agilent.plateloc.plateloc as plateloc_module
from pylabrobot.agilent.plateloc import (
  PlateLoc,
  PlateLocError,
  PlateLocSerialProfile,
  PlateLocStatus,
)
from pylabrobot.io.serial import Serial


class PlateLocTests(unittest.IsolatedAsyncioTestCase):
  serial_constructor: MagicMock

  def make_device(
    self, ack_timeout: float = 0.01, timeout: float = 30
  ) -> tuple[PlateLoc, MagicMock, Deque[int]]:
    responses: Deque[int] = deque()
    io = MagicMock(spec=Serial)
    io.port = "COM6"
    io.temporary_timeout.side_effect = lambda _timeout: contextlib.nullcontext()

    async def read(num_bytes: int = 1) -> bytes:
      if not responses:
        await asyncio.sleep(0)
        return b""
      chunk = bytearray()
      while responses and len(chunk) < num_bytes:
        chunk.append(responses.popleft())
      return bytes(chunk)

    io.read.side_effect = read

    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=ack_timeout,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    with (
      patch.object(plateloc_module, "HAS_SERIAL", True),
      patch.object(plateloc_module, "Serial", return_value=io) as serial_constructor,
    ):
      device = PlateLoc(port="COM6", profile=profile, timeout=timeout)
    self.serial_constructor = serial_constructor
    return device, io, responses

  def assert_writes(self, io: MagicMock, expected: list[bytes]) -> None:
    self.assertEqual([mock_call.args[0] for mock_call in io.write.await_args_list], expected)

  async def test_setup_uses_plr_serial_wrapper_settings(self):
    device, io, _ = self.make_device()

    await device.setup()

    io.setup.assert_awaited_once_with()
    self.serial_constructor.assert_called_once_with(
      human_readable_device_name="Agilent PlateLoc Sealer",
      port="COM6",
      vid=None,
      pid=None,
      baudrate=19200,
      bytesize=8,
      parity="N",
      stopbits=1,
      write_timeout=1,
      timeout=1,
      rtscts=False,
      dsrdtr=False,
      xonxoff=False,
    )

    await device.stop()
    io.stop.assert_awaited_once_with()

  async def test_sends_literal_serial_frame(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"STAK\r")

    response = await device._send_command("ST 0.030", timeout=0.01)

    self.assertEqual(response, "STAK")
    self.assert_writes(io, [b"ST 0.030\r"])
    io.reset_input_buffer.assert_awaited_once_with()

  async def test_serialize_contains_only_connection_configuration(self):
    device, _, _ = self.make_device(timeout=42)

    serialized = device.serialize()

    self.assertEqual(set(serialized), {"port", "profile", "timeout"})
    self.assertEqual(serialized["port"], "COM6")
    self.assertEqual(serialized["timeout"], 42)
    self.assertEqual(serialized["profile"], device.profile.serialize())

  async def test_temperature_write_is_scaled_and_validated(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"STAK\r")

    await device.set_sealing_temperature(30)

    self.assert_writes(io, [b"ST 0.030\r"])

    with self.assertRaises(ValueError):
      await device.set_sealing_temperature(19)

  async def test_negative_acknowledgement_raises_protocol_error(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"STNK(Desired Temperature is Out of Range)\r\r")

    with self.assertRaisesRegex(PlateLocError, "Desired Temperature is Out of Range"):
      await device.set_sealing_temperature(30)

    self.assert_writes(io, [b"ST 0.030\r"])

  async def test_missing_acknowledgement_raises_timeout(self):
    device, io, _ = self.make_device()
    await device.setup()

    with self.assertRaisesRegex(TimeoutError, "Timeout"):
      await device.set_sealing_temperature(30)

    self.assert_writes(io, [b"ST 0.030\r"])

  async def test_malformed_acknowledgement_raises_protocol_error(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await device.set_sealing_temperature(30)

    self.assert_writes(io, [b"ST 0.030\r"])

  async def test_required_response_reads_until_plate_loc_ack(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"CCAK\r")

    self.assertTrue(await device.request_cycle_complete())
    self.assert_writes(io, [b"CC 00\r"])

  async def test_cycle_not_complete_returns_false(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"CCNK\r")

    self.assertFalse(await device.request_cycle_complete())
    self.assert_writes(io, [b"CC 00\r"])

  async def test_invalid_cycle_complete_response_raises_protocol_error(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await device.request_cycle_complete()

    self.assert_writes(io, [b"CC 00\r"])

  async def test_status_snapshot_tracks_setpoints_and_live_cycle_complete(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"STAK\rSSAK\rGOAK\rCCAK\rSOAK\rCCAK\r")

    await device.seal(30, 0.5)
    await device.open()
    status = await device.request_status()

    self.assertIsInstance(status, PlateLocStatus)
    self.assertEqual(status.port, "COM6")
    self.assertTrue(status.connected)
    self.assertEqual(status.target_temperature, 30)
    self.assertEqual(status.stage_position, "open")
    self.assertTrue(status.cycle_complete)
    self.assert_writes(
      io,
      [b"ST 0.030\r", b"SS 0.05\r", b"GO 00\r", b"CC 00\r", b"SO 00\r", b"CC 00\r"],
    )

  async def test_seal_waits_for_cycle_completion(self):
    device, io, responses = self.make_device(timeout=1)
    await device.setup()
    responses.extend(b"STAK\rSSAK\rGOAK\rCCNK\rCCAK\r")

    await device.seal(120, 1.2)

    self.assert_writes(
      io,
      [
        b"ST 0.120\r",
        b"SS 0.12\r",
        b"GO 00\r",
        b"CC 00\r",
        b"CC 00\r",
      ],
    )
    self.assertEqual(device.status_snapshot().target_temperature, 120)

  async def test_seal_validates_duration_before_sending_commands(self):
    device, io, _ = self.make_device()

    with self.assertRaises(ValueError):
      await device.seal(120, 0.4)

    self.assert_writes(io, [])

  async def test_device_exposes_plain_sealer_api(self):
    device, io, responses = self.make_device()
    await device.setup()
    responses.extend(b"STAK\rSTAK\rSSAK\rGOAK\rCCAK\rSOAK\rSIAK\rCCAK\r")

    await device.set_sealing_temperature(100)
    await device.seal(120, 1.2)
    await device.open()
    await device.close()
    status = await device.request_status()
    await device.stop()

    self.assert_writes(
      io,
      [
        b"ST 0.100\r",
        b"ST 0.120\r",
        b"SS 0.12\r",
        b"GO 00\r",
        b"CC 00\r",
        b"SO 00\r",
        b"SI 00\r",
        b"CC 00\r",
      ],
    )
    self.assertEqual(status.target_temperature, 120)
    self.assertEqual(status.stage_position, "closed")
    self.assertTrue(status.cycle_complete)
    io.stop.assert_awaited_once_with()


if __name__ == "__main__":
  unittest.main()
