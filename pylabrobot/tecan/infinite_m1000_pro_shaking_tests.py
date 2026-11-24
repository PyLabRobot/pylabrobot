"""Offline tests for M1000 Pro shaking."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.tecan.infinite_m1000_pro import InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


class LinearShakingTests(unittest.IsolatedAsyncioTestCase):
  async def test_captured_linear_sequence(self):
    """Run the captured ten-second linear sequence through the transport."""
    replies = {
      "?ABSOLUTE MTP,POS": "IN",
      "?STEPABS MTP,X": "1864",
      "?STEPABS MTP,Y": "0",
      "?CONFIG OFFSET,MTP.HEAT.X": "2450",
      "?CONFIG OFFSET,MTP.HEAT.Y": "1000",
    }
    pending = bytearray()
    commands = []

    async def write(data):
      command = data[1 : data.index(b"\x03")].decode("ascii")
      sequence = data[-5]
      commands.append(command)
      if command.startswith("STEPABS ") or command == "SHAKING ON":
        pending.extend(reader_frame("BY#T12000", sequence) + reader_frame("ST", sequence))
      else:
        pending.extend(reader_frame(replies.get(command, "+"), sequence))

    async def read(timeout):
      del timeout
      result = bytes(pending[:7])
      del pending[:7]
      if not result:
        raise TimeoutError("No simulated response")
      return result

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb_class.return_value.write = AsyncMock(side_effect=write)
      usb_class.return_value.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      await reader.shake(10, mode="linear", amplitude=2)
      self.assertEqual(
        commands,
        [
          "?ABSOLUTE MTP,POS",
          "SHAKING MODE=LINEAR",
          "SHAKING TIME=10",
          "SHAKING AMPLITUDE=2000",
          "SHAKING FREQUENCY=109",
          "?STEPABS MTP,X",
          "?STEPABS MTP,Y",
          "?CONFIG OFFSET,MTP.HEAT.X",
          "?CONFIG OFFSET,MTP.HEAT.Y",
          "STEPABS MTP,Y=0",
          "SHAKING ON",
          "STEPABS MTP,Y=0",
        ],
      )
      self.assertFalse(pending)

  async def test_carrier_out_still_shakes(self):
    """A carrier reported OUT is logged, not refused."""
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("OUT"), TimeoutError("stop here")])
      reader = InfiniteM1000Pro()
      reader._ready = True
      with self.assertLogs("pylabrobot.tecan.infinite_m1000_pro", "WARNING"):
        with self.assertRaises(TimeoutError):
          await reader.shake(10, mode="linear", amplitude=2)
      self.assertEqual(usb.write.await_count, 2)

  async def test_interrupted_shake_raises_and_session_stays_usable(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), TimeoutError("lost")])
      reader = InfiniteM1000Pro()
      reader._ready = True
      with self.assertRaises(TimeoutError):
        await reader.shake(10, mode="linear", amplitude=2)
      self.assertTrue(reader._ready)


class ShakeParameterTests(unittest.IsolatedAsyncioTestCase):
  async def test_orbital_sends_time_and_frequency_for_amplitude(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB"):
      reader = InfiniteM1000Pro()
      reader._ready = True
      with (
        patch.object(reader, "_log_position", new=AsyncMock()),
        patch.object(reader, "_query", new=AsyncMock(return_value="0")),
        patch.object(reader, "_control_command", new=AsyncMock()) as control,
      ):
        await reader.shake(30, mode="orbital", amplitude=6)
    calls = [c.args[0] for c in control.await_args_list]
    self.assertEqual(
      calls[:4],
      [
        "SHAKING MODE=ORBITAL",
        "SHAKING TIME=30",
        "SHAKING AMPLITUDE=6000",
        "SHAKING FREQUENCY=20",
      ],
    )
    self.assertEqual(control.await_args_list[5].kwargs["timeout"], 90)

  async def test_unsupported_amplitude_and_duration_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      reader = InfiniteM1000Pro()
      reader._ready = True
      with self.assertRaisesRegex(ValueError, "amplitudes"):
        await reader.shake(10, mode="double", amplitude=4)
      with self.assertRaisesRegex(ValueError, "duration"):
        await reader.shake(0)
      usb_class.return_value.write.assert_not_called()
