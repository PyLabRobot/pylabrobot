"""Offline tests for the captured M1000 Pro linear shaking operation."""

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
      await reader.shake_linear_reference()
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

  async def test_unexpected_position_faults_before_shaking(self):
    """Reject a carrier position that differs from the capture."""
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OUT"))
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      with self.assertRaisesRegex(RuntimeError, "IN"):
        await reader.shake_linear_reference()
      self.assertTrue(reader._faulted)
      self.assertEqual(usb.write.await_count, 1)

  async def test_interrupted_shake_faults_session(self):
    """A transport failure does not leave the session ready."""
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), TimeoutError("lost")])
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      with self.assertRaises(TimeoutError):
        await reader.shake_linear_reference()
      self.assertTrue(reader._faulted)
      self.assertFalse(reader._ready)
