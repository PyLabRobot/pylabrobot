"""Public PREP readbacks round floats without changing raw firmware responses."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pylabrobot.hamilton.prep import PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.calibration import Calibration
from pylabrobot.hamilton.prep.driver.features.pipettes import PipetteChannel, Pipettes
from pylabrobot.hamilton.prep.driver.features.x_arm import XArm
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.resources.hamilton import PrepDeck


class TestRoundedReadbacks(unittest.IsolatedAsyncioTestCase):
  """Exercise result boundaries with non-rounded firmware values and missing readings."""

  async def asyncSetUp(self) -> None:
    """Construct an offline driver; every command is mocked."""
    self.driver = PrepSimulationDriver(deck=PrepDeck())
    await self.driver.setup()
    self.send = AsyncMock()
    self.driver.send_command = self.send  # type: ignore[method-assign]
    self.arm = XArm(self.driver)
    self.pipettes = Pipettes(self.driver)

  async def test_scalar_readbacks(self) -> None:
    """Signed scalar results round to hundredths; absent values stay None."""
    channel = PipetteChannel(
      index=0, driver=self.driver, ydrive=Address(1, 1, 1), zdrive=Address(1, 1, 1)
    )
    for value, expected in ((12.34678, 12.35), (-12.34678, -12.35)):
      self.send.return_value = SimpleNamespace(value=value, position=value)
      for read in (
        self.driver.request_default_traverse_height,
        self.arm.request_commanded_position,
        self.arm.request_speed,
        self.arm.request_acceleration,
        channel.request_y_drive_position,
        channel.request_z_drive_position,
      ):
        self.assertEqual(await read(), expected)
    self.send.return_value = None
    self.assertIsNone(await self.driver.request_default_traverse_height())
    self.assertIsNone(await self.arm.request_position())
    self.send.return_value = SimpleNamespace(positions=[SimpleNamespace(position_x=12.34678)])
    self.assertEqual(await self.arm.request_position(), 12.35)

  async def test_volume_collection_and_single_channel(self) -> None:
    """Both volume APIs round while preserving channel keys."""
    self.send.return_value = SimpleNamespace(
      volumes=[SimpleNamespace(channel=self.pipettes.channel_order[0], volume=12.34678)]
    )
    self.assertEqual(await self.pipettes.dispensing_drives_request_uL_positions(), {0: 12.35})
    self.assertEqual(await self.pipettes.dispensing_drive_request_uL_position(0), 12.35)

  async def test_tip_definitions_are_copied(self) -> None:
    """Round float fields without mutating raw definitions or other fields."""
    tip = PrepCmd.TipDefinition(False, 1, 50.12345, 42.45678, 0, True, False, False, "tip")
    self.send.return_value = SimpleNamespace(definitions=[tip], value=tip)
    definitions = await self.driver.request_tip_and_needle_definitions()
    self.pipettes.sense_tip_presence = AsyncMock(return_value=[True, False])
    attached = await self.pipettes.request_attached_tip_information(0)
    self.assertEqual(attached, definitions[0])
    self.assertEqual((attached.volume, attached.length, attached.label), (50.12, 42.46, "tip"))
    self.assertEqual((tip.volume, tip.length), (50.12345, 42.45678))
    self.assertIsNone(await self.pipettes.request_attached_tip_information(1))
    self.assertEqual(await self.pipettes.request_tip_overhangs(), {0: 42.46, 1: None})
    self.assertEqual(await self.pipettes.request_tip_overhang(0), 42.46)

  async def test_calibration_values(self) -> None:
    """Nested calibration floats round while integer and boolean fields retain their values."""
    channel = PrepCmd.ChannelCalibrationValuesInfo(
      0, 1.23456, -2.34567, 3, 4, 5, 6, 7.45678, 8.56789, True
    )
    self.send.return_value = SimpleNamespace(
      independent_offset_x=9.67891, mph_offset_x=-1.23456, channel_values=[channel]
    )
    values = await Calibration(self.driver).request_calibration_values()
    self.assertEqual((values.independent_offset_x, values.mph_offset_x), (9.68, -1.23))
    self.assertEqual(
      values.channel_values,
      (PrepCmd.ChannelCalibrationValuesInfo(0, 1.23, -2.35, 3, 4, 5, 6, 7.46, 8.57, True),),
    )
    self.assertEqual(channel.y_offset, 1.23456)

  async def test_derived_results(self) -> None:
    """Round arithmetic results after calculation, including binary float residue."""
    self.send.side_effect = [
      SimpleNamespace(positions=[SimpleNamespace(position_x=1.1)]),
      SimpleNamespace(value=0.3),
    ]
    self.assertEqual(await self.arm.request_axis_offset(), 0.8)
    self.pipettes.request_z_position = AsyncMock(return_value=0.1)
    self.pipettes.request_held_tip_length = AsyncMock(return_value=0.2)
    self.pipettes.sense_tip_presence = AsyncMock(return_value=[True, False])
    self.assertEqual(await self.pipettes.request_stop_disc_z_position(0), 0.3)
    self.pipettes.probe_liquid_heights = AsyncMock(return_value=[1.23])
    container = SimpleNamespace(
      compute_volume_from_height=lambda height: height * 1.234,
      supports_compute_height_volume_functions=lambda: True,
    )
    self.assertEqual(await self.pipettes.probe_liquid_volumes([container]), [1.52])
