import unittest
from typing import List, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.cole_parmer.genogrinder import GenoGrinder, GenoGrinderError
from pylabrobot.io.serial import Serial


def make_device(replies: List[str], **kwargs) -> GenoGrinder:
  """Build a device whose ``io`` is an AsyncMock replaying one reply per write.

  Writes are recorded by the mock itself, so assert against ``device.io.write``
  (``assert_any_await``, ``await_count``, ``call_args_list``).
  """
  io = AsyncMock(spec=Serial)
  io.port = "FAKE"

  rx = bytearray()
  pending = list(replies)

  async def write(data: bytes) -> None:
    if pending:
      rx.extend((pending.pop(0) + "\r").encode("ascii"))

  async def read(num_bytes: int = 1) -> bytes:
    out = bytes(rx[:num_bytes])
    del rx[:num_bytes]
    return out

  io.write.side_effect = write
  io.read.side_effect = read

  with patch("pylabrobot.cole_parmer.genogrinder.Serial", return_value=io):
    device = GenoGrinder(
      port="FAKE",
      command_settle=0,
      status_poll_interval=0,
      **kwargs,
    )
  return device


def writes(device: GenoGrinder) -> AsyncMock:
  """The mock standing in for ``device.io.write``."""
  return cast(AsyncMock, device.io.write)


def commands(device: GenoGrinder) -> List[str]:
  """Every command frame written, in order, without its terminator."""
  return [call.args[0].decode("ascii").rstrip("\r") for call in writes(device).call_args_list]


class GenoGrinderProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_initializes_past_the_progress_line(self):
    device = make_device(["Initializing\rInitialize Complete"])
    await device.setup()
    self.assertEqual(commands(device), ["*03*"])

  async def test_setup_initialization_failure_clears_the_error(self):
    device = make_device(["Fault\rFault"])
    with self.assertRaises(GenoGrinderError):
      await device.setup()
    self.assertEqual(commands(device), ["*03*", "*05*"])

  async def test_clamp_state(self):
    device = make_device(["Clamp Open"])
    self.assertEqual(await device.request_clamp_state(), "open")

    device = make_device(["Clamp Closed"])
    self.assertEqual(await device.request_clamp_state(), "closed")

    device = make_device(["Standby"])
    self.assertEqual(await device.request_clamp_state(), "unknown")

  async def test_open_clamp_is_a_no_op_when_already_open(self):
    device = make_device(["Clamp Open"])
    await device.open_clamp()
    self.assertEqual(commands(device), ["*15*"])

  async def test_open_clamp_moves_when_closed(self):
    device = make_device(["Clamp Closed", "Clamp Open"])
    await device.open_clamp()
    self.assertEqual(commands(device), ["*15*", "*11*"])

  async def test_close_clamp_moves_when_open(self):
    device = make_device(["Clamp Open", "Clamp Closed"])
    await device.close_clamp()
    self.assertEqual(commands(device), ["*15*", "*12*"])

  async def test_clamp_commands_suppressed_on_fixed_clamps(self):
    device = make_device([], use_clamp_commands=False)
    await device.open_clamp()
    await device.close_clamp()
    self.assertEqual(commands(device), [])

  async def test_home_clamp(self):
    device = make_device(["Home Complete"])
    await device.home_clamp()
    self.assertEqual(commands(device), ["*10*"])

  async def test_shake_sets_parameters_then_runs_to_completion(self):
    device = make_device(
      [
        "Parameters Set",
        "Running Sample",
        "Mixing",
        "Unlocking",
        "Run Complete",
      ]
    )
    await device.shake(duration=45, speed=1500)
    self.assertEqual(
      commands(device),
      ["*02,045,1500*", "*04*", "*01*", "*01*", "*01*"],
    )

  async def test_shake_rejects_out_of_range_arguments(self):
    device = make_device([])
    with self.assertRaises(ValueError):
      await device.shake(duration=1000)
    with self.assertRaises(ValueError):
      await device.shake(speed=10000)
    self.assertEqual(commands(device), [])

  async def test_shake_raises_on_an_unexpected_state(self):
    device = make_device(["Parameters Set", "Running Sample", "Fault"])
    with self.assertRaises(GenoGrinderError):
      await device.shake(duration=5)

  async def test_shake_times_out(self):
    device = make_device(["Parameters Set", "Running Sample"] + ["Mixing"] * 10)
    device.mix_timeout_margin = -1
    with self.assertRaises(GenoGrinderError):
      await device.shake(duration=1)

  async def test_stop_shaking(self):
    device = make_device(["Standby"])
    await device.stop_shaking()
    self.assertEqual(commands(device), ["*06*"])


if __name__ == "__main__":
  unittest.main()
