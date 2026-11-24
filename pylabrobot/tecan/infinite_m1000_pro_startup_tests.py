"""Simulated startup against the ordered i-control reference exchange."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.tecan.infinite_m1000_pro import InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_absorbance_tests import binary_block
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


class StartupParityTests(unittest.IsolatedAsyncioTestCase):
  async def test_startup_rejects_self_consistent_short_memory_response(self):
    reference = json.loads(
      (Path(__file__).parent / "test_data" / "startup_icontrol.json").read_text(encoding="utf-8")
    )
    pending = bytearray()
    sent = []

    async def write(data):
      command = data[1 : data.index(b"\x03")].decode("ascii")
      sequence = data[-5]
      tx = reference[len(sent) + 2]
      self.assertEqual(command, tx["command"])
      sent.append(command)
      if command == "?MEMORY MAI,0,8":
        pending.extend(binary_block(bytes(7), sequence))
        pending.extend(reader_frame("ST", sequence))
      else:
        for text in tx["text"]:
          pending.extend(reader_frame(text, sequence))

    async def read(timeout):
      del timeout
      if not pending:
        raise TimeoutError("No simulated response")
      data = bytes(pending[:37])
      del pending[:37]
      return data

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as factory:
      usb = factory.return_value
      usb.write = AsyncMock(side_effect=write)
      usb.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      reader._next_sequence = 2
      with self.assertRaisesRegex(ValueError, "length"):
        await reader._startup_reference_sequence()
      self.assertEqual(sent[-1], "?MEMORY MAI,0,8")

  async def test_startup_error_response_is_logged_and_replay_continues(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as factory:
      usb = factory.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST", 0),
          reader_frame("BY#T85000", 1),
          reader_frame("ST", 1),
          reader_frame("ER", 2),
          TimeoutError("stop"),
        ]
      )
      reader = InfiniteM1000Pro()
      with self.assertLogs("pylabrobot.tecan.infinite_m1000_pro", "WARNING"):
        with self.assertRaises(TimeoutError):
          await reader.setup()
      self.assertEqual(usb.write.await_count, 4)
      self.assertTrue(reader._initialized)
      self.assertFalse(reader._startup_done)

  async def test_setup_replays_reference_commands_and_consumes_replies(self):
    reference = json.loads(
      (Path(__file__).parent / "test_data" / "startup_icontrol.json").read_text(encoding="utf-8")
    )
    pending = bytearray()
    sent = []

    async def write(data):
      command = data[1 : data.index(b"\x03")].decode("ascii")
      sequence = data[-5]
      tx = reference[len(sent)]
      self.assertEqual(command, tx["command"])
      sent.append(command)
      sizes = iter(tx["binary_lengths"])
      for text in tx["text"]:
        if text.endswith(",BIN:"):
          size = next(sizes)
          pending.extend(binary_block(bytes(size), sequence))
        else:
          pending.extend(reader_frame(text, sequence))
      self.assertEqual(list(sizes), [])

    async def read(timeout):
      del timeout
      if not pending:
        raise TimeoutError("No simulated response")
      data = bytes(pending[:37])
      del pending[:37]
      return data

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as factory:
      usb = factory.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock(side_effect=write)
      usb.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      await reader.setup()
      self.assertEqual(sent, [tx["command"] for tx in reference])
      self.assertFalse(pending)
      self.assertTrue(reader._initialized)
