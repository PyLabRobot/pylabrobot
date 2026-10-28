import re
import unittest
from typing import Any, List, Optional

from pylabrobot.hamilton.vantage.driver.errors import VantageFirmwareError
from pylabrobot.hamilton.vantage.driver.features.pipettes import Pipettes, PipettesConfiguration
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.hamilton.vantage.driver.master_tests import ScriptedIO, echo, machine
from pylabrobot.legacy.liquid_handling.backends.hamilton.vantage_backend import VantageBackend


def without_id(command: str) -> str:
  """A command without the id it was sent with."""
  return re.sub(r"id\d+", "", command, count=1)


class LegacyCatcher(VantageBackend):
  """The legacy backend, writing what it would send into a list instead of to a machine."""

  def __init__(self, num_channels: int):
    super().__init__()
    self.commands: List[str] = []
    self._num_channels = num_channels

  async def send_command(  # type: ignore[override]
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id, tip_pattern=tip_pattern, **kwargs
    )
    self.commands.append(cmd)


class PipettesTestCase(unittest.IsolatedAsyncioTestCase):
  """A driver on a machine with `num_channels` channels, discovered."""

  num_channels = 8

  async def asyncSetUp(self):
    self.io = ScriptedIO(machine(num_channels=self.num_channels))
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    await self.driver.discover()
    self.io.written.clear()
    pipettes = self.driver.pipettes
    assert pipettes is not None
    self.pipettes: Pipettes = pipettes

  async def asyncTearDown(self):
    self.driver._router.stop()


class TestConfiguration(unittest.TestCase):
  """Converting a value in mm to what the wire carries."""

  def test_a_value_is_written_in_tenths_of_a_millimeter(self):
    configuration = PipettesConfiguration()
    self.assertEqual(configuration.to_increments(245.0), 2450)
    self.assertEqual(configuration.to_increments(123.5), 1235)
    self.assertEqual(configuration.to_increments(0.0), 0)
    self.assertEqual(configuration.to_increments(-7.5), -75)

  def test_a_value_finer_than_the_wire_goes_to_the_nearest_tenth(self):
    configuration = PipettesConfiguration()
    self.assertEqual(configuration.to_increments(100.26), 1003)
    self.assertEqual(configuration.to_increments(100.24), 1002)
    self.assertEqual(configuration.to_increments(-100.26), -1003)

  def test_a_value_with_float_error_is_rounded_to_the_tenth_it_means(self):
    self.assertEqual(PipettesConfiguration().to_increments(389.1), 3891)
    self.assertEqual(PipettesConfiguration().to_increments(201.6), 2016)


class TestQueries(PipettesTestCase):
  """What the channels are asked."""

  async def test_the_tips_come_back_as_booleans_one_for_each_channel(self):
    self.io.answers = machine(tips=[True, False, False, True, False, False, False, False])
    tips = await self.pipettes.request_tip_presence()
    self.assertEqual(tips, [True, False, False, True, False, False, False, False])
    self.assertTrue(all(isinstance(tip, bool) for tip in tips))
    self.assertEqual(self.io.written, ["A1PMQAid0002"])

  async def test_the_initialization_status_is_true_only_for_1(self):
    self.io.answers = machine(channels_initialized=True)
    self.assertTrue(await self.pipettes.request_initialization_status())
    self.io.answers = machine(channels_initialized=False)
    self.assertFalse(await self.pipettes.request_initialization_status())
    self.assertEqual([command[:6] for command in self.io.written], ["A1PMQW", "A1PMQW"])


class TestInitialize(PipettesTestCase):
  """Initializing the channels, which moves them."""

  async def test_by_default_the_wire_carries_what_the_legacy_backend_sends_on_setup(self):
    legacy = LegacyCatcher(self.num_channels)
    await legacy.pip_initialize(
      x_position=[7095] * 8,
      y_position=[3891, 3623, 3355, 3087, 2819, 2551, 2283, 2016],
      begin_z_deposit_position=[2450] * 8,
      end_z_deposit_position=[1235] * 8,
      minimal_height_at_command_end=[2450] * 8,
      tip_pattern=[True] * 8,
      tip_type=[1] * 8,
      TODO_DI_2=70,
    )
    await self.pipettes.initialize(tip_type=[1] * 8)
    self.assertEqual(without_id(self.io.written[0]), without_id(legacy.commands[0]))

  async def test_the_defaults_for_eight_channels(self):
    await self.pipettes.initialize()
    self.assertEqual(
      without_id(self.io.written[0]),
      "A1PMDI"
      "xp"
      + " ".join(["7095"] * 8)
      + "yp3891 3623 3355 3087 2819 2551 2283 2016"
      + "tp"
      + " ".join(["2450"] * 8)
      + "tz"
      + " ".join(["1235"] * 8)
      + "te"
      + " ".join(["2450"] * 8)
      + "tm"
      + " ".join(["1"] * 8)
      + "tt"
      + " ".join(["4"] * 8)
      + "ts70",
    )

  async def test_the_command_goes_to_the_channels_and_is_given_the_time_they_need(self):
    seen = {}
    send = self.driver._router.send

    async def spying(cmd, id_, write_timeout=None, read_timeout=None, wait=True):
      seen[cmd[:6]] = read_timeout
      return await send(cmd, id_, write_timeout, read_timeout, wait)

    self.driver._router.send = spying  # type: ignore[method-assign]
    await self.pipettes.initialize()
    self.assertEqual(seen, {"A1PMDI": 300})

  async def test_what_is_given_is_taken_in_mm_and_converted(self):
    await self.pipettes.initialize(
      x_position=[100.5] * 8,
      y_positions=[10.0 * (i + 1) for i in range(8)],
      begin_of_tip_deposit=[200.0] * 8,
      end_of_tip_deposit=[100.3] * 8,
      minimal_height_at_command_end=[300.0] * 8,
      tip_type=[0, 1, 2, 3, 4, 5, 6, 7],
      x_shift_during_discard=-2.5,
    )
    command = without_id(self.io.written[0])
    self.assertIn("xp" + " ".join(["1005"] * 8), command)
    self.assertIn("yp100 200 300 400 500 600 700 800", command)
    self.assertIn("tp" + " ".join(["2000"] * 8), command)
    self.assertIn("tz" + " ".join(["1003"] * 8), command)
    self.assertIn("te" + " ".join(["3000"] * 8), command)
    self.assertIn("tt0 1 2 3 4 5 6 7", command)
    self.assertTrue(command.endswith("ts-25"), command)

  async def test_channels_can_be_left_out(self):
    await self.pipettes.initialize(tip_pattern=[True] + [False] * 7)
    self.assertIn("tm1 0 0 0 0 0 0 0", without_id(self.io.written[0]))

  async def test_a_list_that_does_not_hold_one_value_for_each_channel_is_refused(self):
    for name in (
      "x_position",
      "y_positions",
      "begin_of_tip_deposit",
      "end_of_tip_deposit",
      "minimal_height_at_command_end",
      "tip_pattern",
      "tip_type",
    ):
      with self.assertRaises(ValueError, msg=name) as caught:
        await self.pipettes.initialize(**{name: [1] * 7})  # type: ignore[arg-type]
      self.assertIn(name, str(caught.exception))
    self.assertEqual(self.io.written, [])

  async def test_a_value_out_of_range_is_refused_and_nothing_is_sent(self):
    refused = (
      {"x_position": [-0.1] * 8},
      {"x_position": [5000.1] * 8},
      {"y_positions": [-0.1] * 8},
      {"y_positions": [650.1] * 8},
      {"begin_of_tip_deposit": [360.1] * 8},
      {"end_of_tip_deposit": [-1.0] * 8},
      {"minimal_height_at_command_end": [361.0] * 8},
      {"tip_type": [200] * 8},
      {"tip_type": [-1] * 8},
      {"x_shift_during_discard": 100.1},
      {"x_shift_during_discard": -100.1},
    )
    for arguments in refused:
      with self.assertRaises(ValueError, msg=str(arguments)):
        await self.pipettes.initialize(**arguments)
    self.assertEqual(self.io.written, [])

  async def test_the_ends_of_every_range_are_accepted(self):
    await self.pipettes.initialize(
      x_position=[0.0] * 4 + [5000.0] * 4,
      y_positions=[0.0] * 4 + [650.0] * 4,
      begin_of_tip_deposit=[0.0] * 4 + [360.0] * 4,
      tip_type=[0] * 4 + [199] * 4,
      x_shift_during_discard=100.0,
    )
    await self.pipettes.initialize(x_shift_during_discard=-100.0)
    self.assertEqual(len(self.io.written), 2)

  async def test_a_reported_error_reaches_the_caller(self):
    self.io.answers = lambda command: [echo(command, 'es"P170"', error=99)]
    with self.assertRaises(VantageFirmwareError):
      await self.pipettes.initialize()


class TestAMachineWithAnotherChannelCount(PipettesTestCase):
  num_channels = 4

  async def test_there_is_no_default_discard_position_for_it(self):
    with self.assertRaises(ValueError) as caught:
      await self.pipettes.initialize()
    self.assertIn("y_positions", str(caught.exception))
    self.assertIn("4 channels", str(caught.exception))
    self.assertEqual(self.io.written, [])

  async def test_it_initializes_where_it_is_told_to(self):
    await self.pipettes.initialize(y_positions=[400.0, 350.0, 300.0, 250.0])
    command = without_id(self.io.written[0])
    self.assertIn("yp4000 3500 3000 2500", command)
    self.assertIn("xp7095 7095 7095 7095", command)
    self.assertIn("tm1 1 1 1", command)
    self.assertNotIn("&", command)

  async def test_the_defaults_are_a_public_table_that_can_be_extended(self):
    self.pipettes.default_y_positions = {4: [400.0, 350.0, 300.0, 250.0]}
    await self.pipettes.initialize()
    self.assertIn("yp4000 3500 3000 2500", self.io.written[0])


class TestBeforeDiscovery(unittest.TestCase):
  def test_the_number_of_channels_is_not_known(self):
    pipettes = Pipettes(VantageDriver(ScriptedIO()))
    with self.assertRaises(RuntimeError):
      pipettes.num_channels  # noqa: B018


if __name__ == "__main__":
  unittest.main()
