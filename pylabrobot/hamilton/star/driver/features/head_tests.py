import dataclasses
import datetime
import json
import pathlib
import random
import tempfile
import unittest
from typing import Any, List, Optional, cast
from unittest.mock import patch

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import RECORDING_STAR, RECORDING_STARLET
from pylabrobot.hamilton.star.driver.configuration import read_configuration
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.head96 import Head96, Head96Configuration
from pylabrobot.hamilton.star.driver.features.x_arm import XArm
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck, STARLetDeck
from pylabrobot.resources.n_channel_pipettes import NChannelPipette
from pylabrobot.serializer import serialize

# The 96-head on the device this package ships a recording of.
RECORDED_HEAD96 = cast(
  Head96Configuration, read_configuration(RECORDING_STAR)["arms"]["left"]["head96"]
)


def declaring(**parts: object) -> str:
  """The shipped recording with parts swapped out, written where it can be read back.

  A declaration is read from a file and nothing else, so a test that needs a device no recording
  describes writes one. Everything not named here stays as the recorded STAR has it.

  Args:
    parts: `device` for the device itself, or a feature name for something the left arm carries.

  Returns:
    The path it was written to.
  """
  tree = json.loads(pathlib.Path(RECORDING_STAR).read_text())
  for name, part in parts.items():
    assert dataclasses.is_dataclass(part) and not isinstance(part, type)
    if name == "device":
      tree["device"] = serialize(dataclasses.asdict(part))
    else:
      tree["arms"]["left"][name] = serialize(dataclasses.asdict(part))
  written = pathlib.Path(tempfile.mkdtemp()) / "declared.json"
  written.write_text(json.dumps(tree))
  return str(written)


class TestDriveDefaults(unittest.IsolatedAsyncioTestCase):
  """Where the value a move uses when the caller names none comes from: what the head reported,
  falling back to what its firmware documents."""

  async def test_the_defaults_are_what_the_head_reported(self):
    """Discovery reads the four Y and Z drive parameters off the head, and the defaults answer with
    them. Read from a head declaring values its firmware does not, so a default that ignored the
    head and computed the documented one instead could not pass. Four distinct values, so a read
    stored under the wrong name fails this too."""
    declared = dataclasses.replace(
      RECORDED_HEAD96,
      y_drive_speed_firmware_reported=200.0,
      y_drive_acceleration_firmware_reported=300.0,
      z_drive_speed_firmware_reported=50.0,
      z_drive_acceleration_firmware_reported=250.0,
    )
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(head96=declared)
    )
    await driver.setup()

    c = cast(Head96, driver.x_arm.head96).configuration
    self.assertEqual(
      (
        c.y_drive_speed_default,
        c.y_drive_acceleration_default,
        c.z_drive_speed_default,
        c.z_drive_acceleration_default,
      ),
      (200.0, 300.0, 50.0, 250.0),
    )

  async def test_the_96_head_takes_its_dispensing_and_squeezer_defaults_too(self):
    """`Head96.discover` reads four drive parameters on top of the Y and Z ones every head shares,
    and its defaults answer with what it reported for them. Apart from the test above because it
    covers the override rather than the base: the 384-head adds no reads of its own.

    Compared to a tenth rather than exactly: the drive counts in increments, so a value that does
    not fall on one comes back as the nearest that does - 400.0 mm/s reads back as 400.01. That is
    what a head does, and what the simulated one does now that its answer crosses the link and is
    decoded rather than handed over whole."""
    declared = dataclasses.replace(
      RECORDED_HEAD96,
      dispensing_drive_speed_firmware_reported=400.0,
      dispensing_drive_acceleration_firmware_reported=9000.0,
      squeezer_drive_speed_firmware_reported=12.0,
      squeezer_drive_acceleration_firmware_reported=50.0,
    )
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(head96=declared)
    )
    await driver.setup()

    c = cast(Head96, driver.x_arm.head96).configuration
    for read, declared_value in zip(
      (
        c.dispensing_drive_speed_default,
        c.dispensing_drive_acceleration_default,
        c.squeezer_drive_speed_default,
        c.squeezer_drive_acceleration_default,
      ),
      (400.0, 9000.0, 12.0, 50.0),
    ):
      self.assertAlmostEqual(read, declared_value, places=1)

  async def test_a_head_that_will_not_say_keeps_what_its_firmware_documents(self):
    """A head that refuses the read leaves discovery with nothing to record, and the defaults fall
    back to the increments its firmware documents rather than the read failing setup. Driven
    through `discover` alone: the rest of setup moves the head, and reads these same parameters to
    do it."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    head = cast(Head96, cast(XArm, driver.left_x_arm).head96)

    async def refuse(parameter: str) -> float:
      raise RuntimeError("this head does not answer for its drives")

    head.request_drive_parameter = refuse  # type: ignore[method-assign]
    await head.discover()

    c = head.configuration
    self.assertEqual(
      (
        c.y_drive_speed_firmware_reported,
        c.y_drive_acceleration_firmware_reported,
        c.z_drive_speed_firmware_reported,
        c.z_drive_acceleration_firmware_reported,
      ),
      (None, None, None, None),
    )
    self.assertEqual(
      (
        c.y_drive_speed_default,
        c.y_drive_acceleration_default,
        c.z_drive_speed_default,
        c.z_drive_acceleration_default,
      ),
      (390.62, 546.88, 85.0, 400.0),
    )


class TestHead96Tips(unittest.IsolatedAsyncioTestCase):
  """As legacy's 96-head tip tests, on the layout they use: a 300 uL filter rack on a STARlet."""

  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking
    from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL_filter

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    self.deck = STARLetDeck()
    self.driver = STARSimulationDriver(
      deck=self.deck, declared_configuration_json=RECORDING_STARLET
    )
    await self.driver.setup()
    self.head = cast(Head96, self.driver.head96)
    self.head_resource = cast(NChannelPipette, self.head.resource)
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(tip_car, track=1)
    self.sent: List[str] = []
    log = self.driver._log_exchange

    def recorded(written: str, read: Optional[str]) -> None:
      if written[:4] in ("C0TT", "H0DQ", "C0EP", "C0ER"):
        self.sent.append(written)
      log(written, read)

    self.driver._log_exchange = recorded  # type: ignore[method-assign]

  async def test_pick_up_and_drop_send_what_legacy_sends(self):
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.tip_rack)
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.deck.get_trash_area96())
    self.assertEqual(
      self.sent,
      [
        "C0TTtt01tf1tl0519tv03600tg2tu0",
        "H0DQdq11281dv13500du00000dr900000dw15",
        "C0EPxs01179xd0yh2418tt01wu0za2164zh2450ze2450",
        "C0ERxs01179xd0yh2418za2164zh2450ze2450",
        "H0DQdq11281dv13500du00000dr900000dw15",
        "C0EPxs01179xd0yh2418tt01wu0za2164zh2450ze2450",
        # The trash drop centres the array; legacy put A1 4.5 mm right and 58.5 mm forward of this.
        "C0ERxs00465xd1yh1788za2164zh2450ze2450",
      ],
    )

  async def test_tips_missing_from_a_rack_are_missing_from_the_head(self):
    rng = random.Random(0)
    shafts = self.head_resource.get_all_items()
    for missing in (0, 1, 48, 95):
      with self.subTest(missing=missing):
        spots = self.tip_rack.get_all_items()
        empty = set(rng.sample(range(96), missing))
        for i in empty:
          spots[i].unassign_tip()
        tips = [spot.tip for spot in spots]

        await self.head.pick_up_tips(self.tip_rack)
        self.assertEqual([shaft.tip for shaft in shafts], tips)
        self.assertFalse(any(spot.has_tip() for spot in spots))

        await self.head.drop_tips(self.tip_rack)
        self.assertEqual([spot.tip for spot in spots], tips)
        self.assertFalse(any(shaft.has_tip() for shaft in shafts))
        self.tip_rack.fill()

  async def test_tips_dropped_in_the_trash_belong_to_nothing(self):
    await self.head.pick_up_tips(self.tip_rack)
    tips = [shaft.tip for shaft in self.head_resource.get_all_items()]
    await self.head.drop_tips(self.deck.get_trash_area96())
    self.assertFalse(any(shaft.has_tip() for shaft in self.head_resource.get_all_items()))
    self.assertFalse(any(spot.has_tip() for spot in self.tip_rack.get_all_items()))
    self.assertTrue(all(tip is not None and tip.parent is None for tip in tips))

  async def test_a_refused_pickup_moves_nothing(self):
    """An empty rack is refused before anything is sent; an unreachable one after the tip type."""
    with self.assertRaises(ValueError):
      await self.head.pick_up_tips(self.tip_rack, offset=Coordinate(0, 1000, 0))
    self.assertEqual(self.sent, ["C0TTtt01tf1tl0519tv03600tg2tu0"])
    self.tip_rack.empty()
    self.sent.clear()
    with self.assertRaises(ValueError):
      await self.head.pick_up_tips(self.tip_rack)
    self.assertEqual(self.sent, [])

  async def test_a_failed_command_leaves_the_tips_where_they_were(self):
    answer = self.driver._answer
    spots = self.tip_rack.get_all_items()
    shafts = self.head_resource.get_all_items()

    def failing(failed: str):
      async def answering(module: str, command: str, **kwargs: Any):
        if command == failed:
          check_fw_string_error(f"C0{failed}id0001er99/00")
        return await answer(module, command, **kwargs)

      return answering

    with patch.object(self.driver, "_answer", failing("EP")):
      with self.assertRaises(STARFirmwareError):
        await self.head.pick_up_tips(self.tip_rack)
    self.assertTrue(all(spot.has_tip() for spot in spots))
    self.assertFalse(any(shaft.has_tip() for shaft in shafts))

    await self.head.pick_up_tips(self.tip_rack)
    with patch.object(self.driver, "_answer", failing("ER")):
      with self.assertRaises(STARFirmwareError):
        await self.head.drop_tips(self.tip_rack)
    self.assertTrue(all(shaft.has_tip() for shaft in shafts))
    self.assertFalse(any(spot.has_tip() for spot in spots))


class TestProbeZUsingCLLD(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking
    from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL_filter

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    self.deck = STARLetDeck()
    self.driver = STARSimulationDriver(
      deck=self.deck, declared_configuration_json=RECORDING_STARLET
    )
    await self.driver.setup()
    self.head = cast(Head96, self.driver.head96)
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[1] = tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(tip_car, track=1)
    await self.head.pick_up_tips(tip_rack)

    self.sent: List[str] = []
    answer = self.driver.send_command

    async def recorded(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      if module + command == "H0ZL":
        self.sent.append(assemble_command(module, command, **kwargs))
        return ""
      if module + command == "H0RH":
        return {"rh": 40000}
      return await answer(module=module, command=command, fmt=fmt, **kwargs)

    self.driver.send_command = recorded  # type: ignore[assignment]

  async def test_the_search_is_sent_in_stop_disc_increments(self):
    detected = await self.head.probe_z_using_clld(tip_overhang=50.0)
    self.assertEqual(
      self.sent,
      ["H0ZLzh36100zc67200zi0400zj1lm2gt0010gl0002zv17000zl02000zr060000zw15"],
    )
    self.assertEqual(detected, 150.0)

  async def test_a_2008_head_refuses_a_sensor(self):
    self.head.configuration.firmware_date = datetime.date(2008, 11, 11)
    with self.assertRaises(ValueError):
      await self.head.probe_z_using_clld(tip_overhang=50.0, lld_sensor="A1 or B2")
    self.assertEqual(self.sent, [])


class TestMix(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking
    from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL_filter
    from pylabrobot.resources.hamilton.plate_carriers import PLT_CAR_L5AC_A00

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    self.deck = STARLetDeck()
    self.driver = STARSimulationDriver(
      deck=self.deck, declared_configuration_json=RECORDING_STARLET
    )
    await self.driver.setup()
    self.head = cast(Head96, self.driver.head96)
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[1] = tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(tip_car, track=1)
    plate_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plate_car[1] = self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.deck.assign_child_resource(plate_car, track=7)
    await self.head.pick_up_tips(tip_rack)

    self.sent: List[str] = []
    answer = self.driver.send_command

    async def recorded(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      if module + command in ("H0PA", "H0PB"):
        self.sent.append(assemble_command(module, command, **kwargs))
        return ""
      return await answer(module=module, command=command, fmt=fmt, **kwargs)

    self.driver.send_command = recorded  # type: ignore[assignment]

  async def test_the_strokes_are_sent_as_legacy_sends_them(self):
    await self.head.mix(self.plate, Mix(volume=100.0, repetitions=2, flow_rate=200.0))
    head = "pmFFFFFFFFFFFFFFFFFFFFFFFF"
    self.assertEqual(
      self.sent,
      [
        f"H0PA{head}dj1da00259dv10341dc00000zd0000zh47710to000",
        f"H0PA{head}dj1da05170dv10341dc00000zd0000zh47730to000",
        f"H0PB{head}db05170dv10341dd0000ze0000zh47730du00000",
        f"H0PA{head}dj1da05170dv10341dc00000zd0000zh47730to000",
        f"H0PB{head}db05170dv10341dd0000ze0000zh47730du00000",
        f"H0PB{head}db00259dv10341dd0000ze0000zh36100du00000",
      ],
    )

  async def test_a_failed_stroke_raises_the_head(self):
    answer = self.driver.send_command

    async def failing(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      if module + command == "H0PB":
        check_fw_string_error("H0PBid0001er99/00")
      return await answer(module, command, fmt=fmt, **kwargs)

    self.driver.send_command = failing  # type: ignore[assignment]
    with self.assertRaises(STARFirmwareError):
      await self.head.mix(self.plate, Mix(volume=100.0, repetitions=1, flow_rate=200.0))
    self.assertEqual(await self.head.request_z_position(), self.head.configuration.z_range[1])

  async def test_a_one_well_plate_gets_the_array_centred_over_it(self):
    from pylabrobot.resources.agenbio.plates import agenbio_1_troughplate_190mL_Fl
    from pylabrobot.resources.hamilton.plate_carriers import PLT_CAR_L5AC_A00

    carrier = PLT_CAR_L5AC_A00(name="trough carrier")
    carrier[0] = trough_plate = agenbio_1_troughplate_190mL_Fl(name="trough")
    self.deck.assign_child_resource(carrier, track=13)
    trough = trough_plate.get_item(0)
    centre = trough.get_location_wrt(self.deck, "c", "c", "b")
    await self.head.mix(trough_plate, Mix(volume=100.0, repetitions=1, flow_rate=200.0))
    c = self.head.configuration
    self.assertAlmostEqual(
      await self.head.request_x_position(), centre.x - c.channel_array_size_x / 2, delta=0.1
    )
    self.assertAlmostEqual(
      await self.head.request_y_position(), centre.y + c.channel_array_size_y / 2, delta=0.1
    )
