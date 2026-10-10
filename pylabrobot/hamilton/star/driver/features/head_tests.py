import dataclasses
import datetime
import json
import pathlib
import random
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Set, Tuple, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import (
  RECORDING_STAR,
  RECORDING_STAR_HEAD384,
  RECORDING_STARLET,
  RECORDING_STARLET_HEAD384,
)
from pylabrobot.hamilton.star.driver.configuration import read_configuration
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.head96 import Head96, Head96Configuration
from pylabrobot.hamilton.star.driver.features.head384 import Head384, Head384Configuration
from pylabrobot.hamilton.star.driver.features.x_arm import XArm
from pylabrobot.hamilton.star.driver.lld_mode import LLDMode
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck, STARLetDeck
from pylabrobot.resources.hamilton.tip_creators import hamilton_tip_50uL
from pylabrobot.resources.n_channel_pipettes import NChannelPipette
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d
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

  async def test_the_96_head_takes_its_squeezer_defaults_too(self):
    """`Head96.discover` reads two drive parameters on top of the Y and Z ones every head shares,
    and its defaults answer with what it reported for them. Apart from the test above because it
    covers the override rather than the base: the 384-head adds no reads of its own.

    Compared to a tenth rather than exactly: the drive counts in increments, so a value that does
    not fall on one comes back as the nearest that does - 400.0 mm/s reads back as 400.01. That is
    what a head does, and what the simulated one does now that its answer crosses the link and is
    decoded rather than handed over whole."""
    declared = dataclasses.replace(
      RECORDED_HEAD96,
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
        c.squeezer_drive_speed_default,
        c.squeezer_drive_acceleration_default,
      ),
      (12.0, 50.0),
    ):
      self.assertAlmostEqual(read, declared_value, places=1)

  async def test_a_head_with_firmware_from_before_2010_gets_its_lower_dispensing_acceleration(self):
    declared = dataclasses.replace(RECORDED_HEAD96, firmware_version="1.0S 2008-11-11 (H0)")
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(head96=declared)
    )
    await driver.setup()
    head = cast(Head96, driver.x_arm.head96)
    self.assertEqual(head.default_dispensing_drive_acceleration, 2900.0)

  async def test_a_default_outside_the_range_of_its_drive_is_refused_when_assigned(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    await driver.setup()
    head = cast(Head96, driver.x_arm.head96)
    with self.assertRaisesRegex(ValueError, "default_dispensing_drive_speed must be between"):
      head.default_dispensing_drive_speed = 5000.0
    with self.assertRaisesRegex(ValueError, "default_dispensing_drive_acceleration must be"):
      head.default_dispensing_drive_acceleration = 50.0
    self.assertEqual(
      (head.default_dispensing_drive_speed, head.default_dispensing_drive_acceleration),
      (250.0, 17000.0),
    )
    head.default_dispensing_drive_speed = 200.0
    self.assertEqual(head.default_dispensing_drive_speed, 200.0)

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

  async def test_a_covered_rack_is_not_picked_up_from_nor_dropped_into(self):
    from pylabrobot.resources.lid import Lid

    self.tip_rack.lid = Lid("lid", size_x=127.0, size_y=86.0, size_z=10.0, nesting_z_height=2.0)
    with self.assertRaisesRegex(ValueError, "pick up tips from 'tip_rack_01': it is not available"):
      await self.head.pick_up_tips(self.tip_rack)
    self.assertEqual(self.sent, [])

    self.tip_rack.lid.unassign()
    await self.head.pick_up_tips(self.tip_rack)
    self.tip_rack.lid = Lid("lid", size_x=127.0, size_y=86.0, size_z=10.0, nesting_z_height=2.0)
    self.sent.clear()
    with self.assertRaisesRegex(ValueError, "drop tips into 'tip_rack_01': it is not available"):
      await self.head.drop_tips(self.tip_rack)
    self.assertEqual(self.sent, [])

  async def test_pick_up_and_drop_send_what_legacy_sends(self):
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.tip_rack)
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.deck.get_trash_area96())
    self.assertEqual(
      self.sent,
      [
        "C0TTtt01tf1tl0519tv03600tg2tu0",
        "H0DQdq11281dv12926du00000dr878965dw15",
        "C0EPxs01179xd0yh2418tt01wu0za2164zh2450ze2450",
        "C0ERxs01179xd0yh2418za2164zh2450ze2450",
        "H0DQdq11281dv12926du00000dr878965dw15",
        "C0EPxs01179xd0yh2418tt01wu0za2164zh2450ze2450",
        # The trash drop centres the array; legacy put A1 4.5 mm right and 58.5 mm forward of this.
        "C0ERxs00465xd1yh1788za2164zh2450ze2450",
      ],
    )

  async def test_a_pickup_with_tips_on_refuses_before_anything_moves(self):
    await self.head.pick_up_tips(self.tip_rack)
    self.sent.clear()
    with self.assertRaises(RuntimeError):
      await self.head.pick_up_tips(self.tip_rack)
    self.assertEqual(self.sent, [])

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

  def failing_on(self, failed: str):
    answer = self.driver._answer

    async def answering(module: str, command: str, **kwargs: Any):
      if command == failed:
        check_fw_string_error(f"C0{failed}id0001er99/00")
      return await answer(module, command, **kwargs)

    return patch.object(self.driver, "_answer", answering)

  async def test_a_failed_pickup_takes_what_the_firmware_holds_and_reads_where_it_stopped(self):
    shafts = self.head_resource.get_all_items()
    # The guard reads no tips; after the failure the firmware holds that they are on.
    held = AsyncMock(side_effect=[False, True])
    read_z = AsyncMock(wraps=self.head.request_z_position)
    with self.failing_on("EP"), patch.object(self.head, "request_tip_presence", held):
      with patch.object(self.head, "request_z_position", read_z):
        with self.assertRaises(STARFirmwareError):
          await self.head.pick_up_tips(self.tip_rack)
    self.assertTrue(all(shaft.has_tip() for shaft in shafts))
    self.assertFalse(any(spot.has_tip() for spot in self.tip_rack.get_all_items()))
    read_z.assert_awaited()

  async def test_a_failed_drop_takes_what_the_firmware_holds(self):
    await self.head.pick_up_tips(self.tip_rack)
    held = AsyncMock(return_value=False)
    with self.failing_on("ER"), patch.object(self.head, "request_tip_presence", held):
      with self.assertRaises(STARFirmwareError):
        await self.head.drop_tips(self.tip_rack)
    self.assertFalse(any(shaft.has_tip() for shaft in self.head_resource.get_all_items()))
    self.assertTrue(all(spot.has_tip() for spot in self.tip_rack.get_all_items()))

  async def test_the_piston_is_read_after_a_pickup_and_a_drop(self):
    commands: List[str] = []
    log = self.driver._log_exchange

    def every(written: str, read: Optional[str]) -> None:
      commands.append(written[:4])
      log(written, read)

    self.driver._log_exchange = every  # type: ignore[method-assign]
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.tip_rack)
    for command in ("C0EP", "C0ER"):
      self.assertIn("H0RD", commands[commands.index(command) :])
    self.assertEqual(commands[-1], "H0RD")

  async def test_return_tips_drops_them_in_the_rack_they_came_from(self):
    """Spots left empty before the pickup stay empty after the return."""
    for i in (0, 47):
      self.tip_rack.get_item(i).unassign_tip()
    tips = [spot.tip for spot in self.tip_rack.get_all_items()]
    await self.head.pick_up_tips(self.tip_rack)
    self.sent.clear()
    await self.head.return_tips()
    self.assertEqual(self.sent, ["C0ERxs01179xd0yh2418za2164zh2450ze2450"])
    self.assertEqual([spot.tip for spot in self.tip_rack.get_all_items()], tips)
    self.assertFalse(any(shaft.has_tip() for shaft in self.head_resource.get_all_items()))

  async def test_discard_tips_drops_them_in_the_96_trash(self):
    await self.head.pick_up_tips(self.tip_rack)
    self.sent.clear()
    await self.head.discard_tips()
    self.assertEqual(self.sent, ["C0ERxs00465xd1yh1788za2164zh2450ze2450"])
    self.assertFalse(any(shaft.has_tip() for shaft in self.head_resource.get_all_items()))

  async def test_with_no_tips_return_refuses_before_anything_is_sent(self):
    with self.assertRaises(RuntimeError):
      await self.head.return_tips()
    self.assertEqual(self.sent, [])

  async def test_return_tips_refuses_tips_from_two_racks(self):
    from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL_filter

    other_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_02")
    cast(Any, self.deck.get_resource("tip carrier"))[2] = other_rack
    await self.head.pick_up_tips(self.tip_rack)
    shaft = self.head_resource.get_item(5)
    shaft.release_tip()
    shaft.mount_tip(other_rack.get_item(5).tip_for_pickup())
    self.sent.clear()
    with self.assertRaisesRegex(RuntimeError, "not from spot 5 of tip_rack_01"):
      await self.head.return_tips()
    self.assertEqual(self.sent, [])

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

  async def test_each_stroke_follows_what_one_draw_takes_from_the_well(self):
    for well in self.plate.get_all_items():
      well.tracker.set_volume(300.0)
    await self.head.mix(
      self.plate, Mix(volume=100.0, repetitions=1, flow_rate=200.0, auto_surface_following=True)
    )
    well = self.plate.get_item("A1")
    drop = round(well.compute_height_from_volume(300.0) - well.compute_height_from_volume(200.0), 1)
    following = self.head.configuration.z_drive_mm_to_increments(drop)
    self.assertIn(f"zd{following:04}", self.sent[1])
    self.assertIn(f"ze{following:04}", self.sent[2])

  async def test_auto_surface_following_needs_a_surface(self):
    with self.assertRaises(ValueError):
      Mix(
        volume=100.0,
        repetitions=1,
        flow_rate=200.0,
        surface_following_distance=1.0,
        auto_surface_following=True,
      )
    auto = Mix(volume=100.0, repetitions=1, flow_rate=200.0, auto_surface_following=True)
    with self.assertRaises(ValueError):
      await self.head.mix(self.plate, auto, lld_mode=LLDMode.OFF)
    self.assertEqual(self.sent, [])

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

  async def _record_lld(
    self, rh: Optional[int] = None, zl_error: Optional[str] = None
  ) -> List[str]:
    """Answer H0 ZL and RH as given, and record every PA, PB, ZL, RH and head ZA, in order."""
    answer = self.driver.send_command
    order: List[str] = []

    async def lld(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      if module == "H0" and command in ("ZL", "RH", "PA", "PB", "ZA"):
        wire = {key: value for key, value in kwargs.items() if len(key) == 2}
        order.append(assemble_command(module, command, **wire))
      if module + command == "H0ZL":
        if zl_error is not None:
          check_fw_string_error(zl_error)
        return ""
      if module + command == "H0RH":
        return {"rh": rh}
      if module + command in ("H0PA", "H0PB"):
        return ""
      return await answer(module, command, fmt=fmt, **kwargs)

    self.driver.send_command = lld  # type: ignore[assignment]
    return order

  async def test_under_clld_the_air_goes_in_first_and_the_head_rises_once(self):
    well = self.plate.get_item("A1")
    bottom = well.get_location_wrt(self.deck, "c", "c", "cavity_bottom").z
    overhang = await self.head._overhang_that_probes()
    c = self.head.configuration
    order = await self._record_lld(rh=c.z_drive_mm_to_increments(bottom + 5.0 + overhang))
    await self.head.mix(
      self.plate,
      Mix(volume=50.0, repetitions=2, flow_rate=100.0),
      lld_mode=LLDMode.CAPACITIVE,
    )
    self.assertEqual(
      [command[2:4] for command in order if command[2:4] != "ZA"],
      ["PA", "ZL", "RH", "PA", "PB", "PA", "PB", "PB"],
    )
    zl = next(command for command in order if command.startswith("H0ZL"))
    self.assertIn(f"zh{c.z_drive_mm_to_increments(bottom + overhang):05}", zl)
    self.assertIn("zi0000", zl)
    # Down from the surface to 2 mm under it, then the strokes; the rise comes before the air out.
    moves = [command for command in order if command.startswith("H0ZA")]
    self.assertIn(f"za{c.z_drive_mm_to_increments(bottom + 3.0 + overhang):05}", moves[-2])
    self.assertTrue(order.index(moves[-1]) < len(order) - 1 and order[-1].startswith("H0PB"))
    self.assertAlmostEqual(well.tracker.get_used_volume(), well.compute_volume_from_height(5.0))

  async def test_under_clld_no_liquid_raises_and_the_head_goes_up(self):
    await self._record_lld(zl_error="H0ZLid0001er70")
    with self.assertRaises(RuntimeError):
      await self.head.mix(
        self.plate,
        Mix(volume=50.0, repetitions=1, flow_rate=100.0),
        lld_mode=LLDMode.CAPACITIVE,
      )
    self.assertEqual(await self.head.request_z_position(), self.head.configuration.z_range[1])

  async def test_probe_liquid_height_averages_the_rounds_from_the_cavity_bottom(self):
    well = self.plate.get_item("A1")
    bottom = well.get_location_wrt(self.deck, "c", "c", "cavity_bottom").z
    overhang = await self.head._overhang_that_probes()
    c = self.head.configuration
    order = await self._record_lld(rh=c.z_drive_mm_to_increments(bottom + 4.0 + overhang))
    height = await self.head.probe_liquid_height(self.plate, n_replicates=2)
    self.assertAlmostEqual(height, 4.0, delta=0.01)
    self.assertEqual(
      [command[2:4] for command in order if command[2:4] in ("ZL", "RH")], ["ZL", "RH"] * 2
    )
    self.assertEqual(await self.head.request_z_position(), c.z_range[1])

  async def test_probe_liquid_height_is_zero_where_nothing_is_met(self):
    await self._record_lld(zl_error="H0ZLid0001er70")
    self.assertEqual(await self.head.probe_liquid_height(self.plate), 0.0)

  async def test_probe_liquid_volume_is_the_volume_at_the_height(self):
    well = self.plate.get_item("A1")
    bottom = well.get_location_wrt(self.deck, "c", "c", "cavity_bottom").z
    overhang = await self.head._overhang_that_probes()
    c = self.head.configuration
    await self._record_lld(rh=c.z_drive_mm_to_increments(bottom + 4.0 + overhang))
    volume = await self.head.probe_liquid_volume(self.plate)
    self.assertAlmostEqual(volume, well.compute_volume_from_height(4.0), delta=1.0)


class TestHead96InSimulation(unittest.IsolatedAsyncioTestCase):
  """The simulator answers the head's searches and strokes from the model, with no stubs."""

  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking
    from pylabrobot.resources.agenbio.plates import agenbio_1_troughplate_190mL_Fl
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
    plate_car[3] = self.trough_plate = agenbio_1_troughplate_190mL_Fl(name="trough")
    self.deck.assign_child_resource(plate_car, track=7)
    await self.head.pick_up_tips(tip_rack)

  def height_of(self, container, volume: float) -> float:
    return float(round(container.compute_height_from_volume(volume), 2))

  async def test_the_surface_is_read_from_the_tracker(self):
    well = self.plate.get_item("A1")
    well.tracker.set_volume(150.0)
    height = await self.head.probe_liquid_height(self.plate)
    self.assertAlmostEqual(height, self.height_of(well, 150.0), delta=0.02)

  async def test_rh_is_the_stop_disc_zi_below_where_the_head_stops(self):
    self.plate.get_item("A1").tracker.set_volume(150.0)
    # The search does not move in X or Y: the head goes over the plate first.
    await self.head._move_over(self.head._get_target(self.plate, None)[1], None, None)
    await self.head.probe_z_using_clld(post_detection_distance=4.0)
    self.assertAlmostEqual(
      await self.head.request_z_position(),
      await self.head.request_last_lld_z_position() + 4.0,
      delta=0.01,
    )

  async def test_an_empty_well_reads_zero(self):
    self.assertEqual(await self.head.probe_liquid_height(self.plate), 0.0)

  async def test_a_one_well_trough_is_found_with_the_array_centred(self):
    trough = self.trough_plate.get_item(0)
    trough.tracker.set_volume(100_000.0)
    height = await self.head.probe_liquid_height(self.trough_plate)
    self.assertAlmostEqual(height, self.height_of(trough, 100_000.0), delta=0.02)

  async def test_a_sensor_reads_only_its_own_corner(self):
    g11 = self.plate.get_item("G11")
    g11.tracker.set_volume(150.0)
    self.assertEqual(await self.head.probe_liquid_height(self.plate, lld_sensor="A1 or B2"), 0.0)
    height = await self.head.probe_liquid_height(self.plate, lld_sensor="G11 or H12")
    self.assertAlmostEqual(height, self.height_of(g11, 150.0), delta=0.02)

  async def test_a_mix_moves_the_piston_and_leaves_the_liquid(self):
    trough = self.trough_plate.get_item(0)
    trough.tracker.set_volume(100_000.0)
    mix = Mix(volume=50.0, repetitions=3, flow_rate=100.0, surface_following_distance=1.0)
    await self.head.mix(self.trough_plate, mix, offset=Coordinate(0, 0, 5.0))
    self.assertAlmostEqual(self.driver.head96_dispensing_drive_uL, 0.0, delta=0.05)
    self.assertEqual(trough.tracker.get_used_volume(), 100_000.0)

  async def test_a_mix_under_clld_runs_end_to_end(self):
    trough = self.trough_plate.get_item(0)
    trough.tracker.set_volume(100_000.0)
    await self.head.mix(
      self.trough_plate,
      Mix(volume=50.0, repetitions=2, flow_rate=100.0),
      lld_mode=LLDMode.CAPACITIVE,
    )
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 100_000.0, delta=50.0)
    self.assertEqual(await self.head.request_z_position(), self.head.configuration.z_range[1])


class _WireOnly:
  """A driver that records what it is asked to send and answers nothing."""

  def __init__(self) -> None:
    self.sent: List[str] = []

  async def send_command(self, module: str, command: str, **kwargs: Any) -> str:
    wire = {key: value for key, value in kwargs.items() if len(key) == 2}
    self.sent.append(assemble_command(module, command, **wire))
    return ""


async def _get_legacy_wire(command: str, **kwargs: Any) -> str:
  """What legacy's `aspirate_core_96` (`EA`) or `dispense_core_96` (`ED`) assembles for kwargs."""
  from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import STARBackend
  from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox import (
    _DEFAULT_EXTENDED_CONFIGURATION,
  )

  backend = STARBackend()
  backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
  backend._iswap_parked = True
  sent = AsyncMock(return_value="")
  backend.send_command = sent  # type: ignore[method-assign]
  if command == "EA":
    await backend.aspirate_core_96(**kwargs)
  else:
    await backend.dispense_core_96(**kwargs)
  wire = {key: value for key, value in sent.call_args.kwargs.items() if len(key) == 2}
  return assemble_command(sent.call_args.kwargs["module"], command, **wire)


# Legacy's name for each field, this driver's, and the range legacy accepts.
_EA_FIELDS = [
  ("aspiration_type", "aspiration_type", 0, 2),
  ("y_positions", "y_position", 1080, 5600),
  ("minimum_traverse_height_at_beginning_of_a_command", "minimum_traverse_height_start", 0, 3425),
  ("min_z_endpos", "minimum_z_end_position", 0, 3425),
  ("lld_search_height", "lld_search_height", 0, 3425),
  ("liquid_surface_no_lld", "liquid_surface_no_lld", 0, 3425),
  ("pull_out_distance_transport_air", "pull_out_distance_transport_air", 0, 3425),
  ("second_section_height", "second_section_height", 0, 3425),
  ("second_section_ratio", "second_section_ratio", 0, 10000),
  ("minimum_height", "minimum_height", 0, 3425),
  ("immersion_depth", "immersion_depth", 0, 3600),
  ("immersion_depth_direction", "immersion_depth_direction", 0, 1),
  ("surface_following_distance", "surface_following_distance", 0, 990),
  ("aspiration_volumes", "aspiration_volume", 0, 11500),
  ("aspiration_speed", "aspiration_speed", 3, 5000),
  ("transport_air_volume", "transport_air_volume", 0, 500),
  ("blow_out_air_volume", "blow_out_air_volume", 0, 11500),
  ("pre_wetting_volume", "pre_wetting_volume", 0, 11500),
  ("lld_mode", "lld_mode", 0, 4),
  ("gamma_lld_sensitivity", "clld_sensitivity", 1, 4),
  ("swap_speed", "swap_speed", 3, 1000),
  ("settling_time", "settling_time", 0, 99),
  ("mix_volume", "mix_volume", 0, 11500),
  ("mix_cycles", "mix_cycles", 0, 99),
  ("mix_position_from_liquid_surface", "mix_position_from_liquid_surface", 0, 990),
  ("speed_of_mix", "mix_speed", 3, 5000),
  ("mix_surface_following_distance", "mix_surface_following_distance", 0, 990),
  ("limit_curve_index", "limit_curve_index", 0, 999),
  ("recording_mode", "recording_mode", 0, 2),
]
_ED_FIELDS = [
  ("dispensing_mode", "dispensing_mode", 0, 4),
  ("y_position", "y_position", 1080, 5600),
  ("minimum_height", "minimum_height", 0, 3425),
  ("lld_search_height", "lld_search_height", 0, 3425),
  ("liquid_surface_no_lld", "liquid_surface_no_lld", 0, 3425),
  ("pull_out_distance_transport_air", "pull_out_distance_transport_air", 0, 3425),
  ("immersion_depth", "immersion_depth", 0, 3600),
  ("immersion_depth_direction", "immersion_depth_direction", 0, 1),
  ("surface_following_distance", "surface_following_distance", 0, 990),
  ("second_section_height", "second_section_height", 0, 3425),
  ("second_section_ratio", "second_section_ratio", 0, 10000),
  ("minimum_traverse_height_at_beginning_of_a_command", "minimum_traverse_height_start", 0, 3425),
  ("min_z_endpos", "minimum_z_end_position", 0, 3425),
  ("dispense_volume", "dispense_volume", 0, 11500),
  ("dispense_speed", "dispense_speed", 3, 5000),
  ("cut_off_speed", "cut_off_speed", 3, 5000),
  ("stop_back_volume", "stop_back_volume", 0, 999),
  ("transport_air_volume", "transport_air_volume", 0, 500),
  ("blow_out_air_volume", "blow_out_air_volume", 0, 11500),
  ("lld_mode", "lld_mode", 0, 4),
  ("side_touch_off_distance", "side_touch_off_distance", 0, 45),
  ("gamma_lld_sensitivity", "clld_sensitivity", 1, 4),
  ("swap_speed", "swap_speed", 3, 1000),
  ("settling_time", "settling_time", 0, 99),
  ("mixing_volume", "mix_volume", 0, 11500),
  ("mixing_cycles", "mix_cycles", 0, 99),
  ("mix_position_from_liquid_surface", "mix_position_from_liquid_surface", 0, 990),
  ("speed_of_mixing", "mix_speed", 3, 5000),
  ("mix_surface_following_distance", "mix_surface_following_distance", 0, 990),
  ("limit_curve_index", "limit_curve_index", 0, 999),
  ("recording_mode", "recording_mode", 0, 2),
]


def _get_value_sets(fields: List[Tuple[str, str, int, int]]) -> List[Tuple[dict, dict]]:
  """Three (legacy kwargs, this driver's kwargs) pairs: every field low, then high, then random."""
  rng = random.Random(0)
  pairs = []
  for draw in ("low", "high", "random"):
    legacy: Dict[str, Any] = {}
    ours: Dict[str, Any] = {}
    for legacy_name, name, low, high in fields:
      value = {"low": low, "high": high, "random": rng.randint(low, high)}[draw]
      legacy[legacy_name], ours[name] = value, value
    x = {"low": 0, "high": 30000, "random": rng.randint(1, 30000)}[draw]
    direction = 0 if draw == "low" else 1
    pattern = {
      "low": [False] * 96,
      "high": [True] * 96,
      "random": [rng.random() < 0.5 for _ in range(96)],
    }[draw]
    tadm = draw != "low"
    legacy.update(x_position=x, x_direction=direction, channel_pattern=pattern, tadm_algorithm=tadm)
    ours.update(x_position=-x if direction else x, channel_pattern=pattern, tadm_algorithm=tadm)
    pairs.append((legacy, ours))
  return pairs


class TestHead96AspirateDispenseWire(unittest.IsolatedAsyncioTestCase):
  """`C0 EA` and `C0 ED` go out as legacy's `aspirate_core_96` and `dispense_core_96` send them."""

  async def test_aspirate_is_sent_as_legacy_sends_it(self):
    for legacy, ours in _get_value_sets(_EA_FIELDS):
      with self.subTest(values=ours):
        driver = _WireOnly()
        await Head96(cast(Any, driver))._unchecked_fw_aspirate(**ours)
        self.assertEqual(driver.sent, [await _get_legacy_wire("EA", **legacy)])

  async def test_dispense_is_sent_as_legacy_sends_it(self):
    for legacy, ours in _get_value_sets(_ED_FIELDS):
      with self.subTest(values=ours):
        driver = _WireOnly()
        await Head96(cast(Any, driver))._unchecked_fw_dispense(**ours)
        self.assertEqual(driver.sent, [await _get_legacy_wire("ED", **legacy)])


class TestHead96PistonModel(unittest.IsolatedAsyncioTestCase):
  """`piston_position` follows every stroke, and a failed one is read back from `H0 RD`."""

  async def asyncSetUp(self):
    self.driver = STARSimulationDriver(
      deck=STARLetDeck(), declared_configuration_json=RECORDING_STARLET
    )
    await self.driver.setup()
    self.head = cast(Head96, self.driver.head96)
    self.rd = [0, 0]
    self.failing: Optional[str] = None
    answer = self.driver.send_command

    async def stubbed(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
      if module + command == "H0RD":
        return {"rd": list(self.rd)}
      if module + command in ("H0PA", "H0PB", "H0DQ"):
        if module + command == self.failing:
          check_fw_string_error(f"{module}{command}id0001er99/00")
        return ""
      return await answer(module, command, fmt=fmt, **kwargs)

    self.driver.send_command = stubbed  # type: ignore[assignment]

  def as_counted(self, uL: float) -> float:
    c = self.head.configuration
    return c.dispensing_drive_increments_to_uL(c.dispensing_drive_uL_to_increments(uL))

  async def test_the_read_takes_the_hardware_counter(self):
    self.rd = [1, 5170]
    expected = self.head.configuration.dispensing_drive_increments_to_uL(5170)
    self.assertEqual(await self.head.dispensing_drive_request_uL_position(), expected)
    self.assertEqual(self.head.piston_position, expected)

  async def test_initialization_reads_the_piston(self):
    self.rd = [0, 1000]
    await self.head.initialize(tip_discard_location=Coordinate(400.0, 300.0, 200.0))
    expected = self.head.configuration.dispensing_drive_increments_to_uL(1000)
    self.assertEqual(self.head.piston_position, expected)

  async def test_strokes_move_the_model_as_the_drive_counts(self):
    await self.head._aspirate_in_place(100.0)
    self.assertEqual(self.head.piston_position, self.as_counted(100.0))
    await self.head._dispense_in_place(60.0, stop_back_volume=2.0)
    expected = self.as_counted(100.0) - self.as_counted(60.0) + self.as_counted(2.0)
    self.assertAlmostEqual(self.head.piston_position, expected, places=2)
    # Pushing out more than it holds leaves the piston at rest.
    await self.head._dispense_in_place(500.0)
    self.assertEqual(self.head.piston_position, 0.0)

  async def test_the_dispensing_drive_move_records_where_it_was_sent(self):
    await self.head.move_dispensing_drive_to_position(218.19)
    self.assertEqual(self.head.piston_position, self.as_counted(218.19))

  async def test_a_failed_command_reads_the_piston_back(self):
    moves = {
      "H0PA": lambda: self.head._aspirate_in_place(100.0),
      "H0PB": lambda: self.head._dispense_in_place(100.0),
      "H0DQ": lambda: self.head.move_dispensing_drive_to_position(100.0),
    }
    for failing, move in moves.items():
      with self.subTest(command=failing):
        self.failing = failing
        self.rd = [0, 1234]
        self.head.piston_position = 50.0
        with self.assertRaises(STARFirmwareError):
          await move()
        expected = self.head.configuration.dispensing_drive_increments_to_uL(1234)
        self.assertEqual(self.head.piston_position, expected)


class TestHead96AspirateDispense(unittest.IsolatedAsyncioTestCase):
  """`aspirate` and `dispense` over a plate and a one-well trough, the raw commands recorded."""

  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking, set_volume_tracking
    from pylabrobot.resources.agenbio.plates import agenbio_1_troughplate_190mL_Fl
    from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL_filter
    from pylabrobot.resources.hamilton.plate_carriers import PLT_CAR_L5AC_A00

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    set_volume_tracking(True)
    self.addCleanup(set_volume_tracking, False)
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
    plate_car[3] = trough_plate = agenbio_1_troughplate_190mL_Fl(name="trough")
    self.deck.assign_child_resource(plate_car, track=7)
    self.trough = trough_plate.get_item(0)
    await self.head.pick_up_tips(tip_rack)
    # Where the piston stands is read, never moved: the simulator leaves it where it started.
    await self.head.dispensing_drive_request_uL_position()
    shafts = cast(NChannelPipette, self.head.resource).get_all_items()
    self.tips = [cast(Tip, shaft.tip) for shaft in shafts]
    for well in self.plate.get_all_items():
      well.tracker.set_volume(200.0)

    self.sent: List[Tuple[str, Dict[str, Any]]] = []
    self.failing = False
    # How far a failed command moves the piston before it stops, in uL.
    self.moved_before_failure = 0.0
    self.moved = 0.0

    def recorder(command: str):
      async def record(**kwargs: Any) -> None:
        self.sent.append((command, kwargs))
        if self.failing:
          self.moved = self.moved_before_failure
          check_fw_string_error(f"C0{command}id0001er99/00")

      return record

    async def piston() -> float:
      return self.head.piston_position + self.moved

    self.head._unchecked_fw_aspirate = recorder("EA")  # type: ignore[method-assign]
    self.head._unchecked_fw_dispense = recorder("ED")  # type: ignore[method-assign]
    self.head.dispensing_drive_request_uL_position = piston  # type: ignore[method-assign]

  def tip_volumes(self) -> Set[float]:
    return {tip.tracker.get_used_volume() for tip in self.tips}

  def well_volumes(self) -> Set[float]:
    return {well.tracker.get_used_volume() for well in self.plate.get_all_items()}

  async def test_off_over_a_plate_draws_at_the_cavity_bottom_and_books_each_well(self):
    await self.head.aspirate(self.plate, 50.0)
    self.assertEqual(
      self.sent,
      [
        (
          "EA",
          {
            "aspiration_type": 0,
            "x_position": 2533,
            "y_position": 2417,
            "minimum_traverse_height_start": 2450,
            "minimum_z_end_position": 2450,
            "lld_search_height": 2018,
            "liquid_surface_no_lld": 1866,
            "pull_out_distance_transport_air": 100,
            "minimum_height": 1866,
            "second_section_height": 32,
            "second_section_ratio": 6180,
            "immersion_depth": 0,
            "immersion_depth_direction": 0,
            "surface_following_distance": 0,
            # 50 uL, corrected by the water class of a 300 uL CoRe filter tip.
            "aspiration_volume": 534,
            "aspiration_speed": 1000,
            "transport_air_volume": 0,
            "blow_out_air_volume": 0,
            "pre_wetting_volume": 50,
            "lld_mode": 0,
            "clld_sensitivity": 1,
            "swap_speed": 20,
            "settling_time": 10,
            "mix_volume": 0,
            "mix_cycles": 0,
            "mix_position_from_liquid_surface": 0,
            "mix_surface_following_distance": 0,
            "mix_speed": 1000,
            "channel_pattern": [True] * 96,
            "limit_curve_index": 0,
            "tadm_algorithm": False,
            "recording_mode": 0,
          },
        )
      ],
    )
    self.assertEqual(self.well_volumes(), {150.0})
    self.assertEqual(self.tip_volumes(), {50.0})
    self.assertEqual(self.head.piston_position, 53.4)

  async def test_off_into_a_trough_centres_the_array_and_books_96_times(self):
    await self.head.aspirate(self.plate, piston_volume=40.0, liquid_height=2.0)
    self.assertEqual(self.sent[0][1]["liquid_surface_no_lld"], 1886)
    await self.head.dispense(self.trough, piston_volume=40.0, jet=True, blow_out=True)
    command, fields = self.sent[1]
    self.assertEqual(command, "ED")
    centre = self.trough.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
    c = self.head.configuration
    self.assertEqual(
      (fields["dispensing_mode"], fields["x_position"], fields["y_position"]),
      (
        1,
        round((centre.x - c.channel_array_size_x / 2) * 10),
        round((centre.y + c.channel_array_size_y / 2) * 10),
      ),
    )
    self.assertEqual(
      (fields["liquid_surface_no_lld"], fields["minimum_height"], fields["dispense_volume"]),
      (round(centre.z * 10), round(centre.z * 10), 400),
    )
    self.assertEqual(self.trough.tracker.get_used_volume(), 96 * 40.0)
    self.assertEqual(self.tip_volumes(), {0.0})
    self.assertEqual(self.head.piston_position, 0.0)

  async def test_capacitive_draws_the_air_first_then_under_the_surface_found(self):
    self.trough.tracker.set_volume(100_000.0)
    await self.head.aspirate(
      self.trough,
      piston_volume=20.0,
      lld_mode=LLDMode.CAPACITIVE,
      blow_out_air_volume=10.0,
      auto_surface_following=True,
    )
    fields = self.sent[0][1]
    bottom = self.trough.get_location_wrt(self.deck, "c", "c", "cavity_bottom").z
    height = self.trough.compute_height_from_volume(100_000.0)
    # The air goes in in place beforehand; the command starts where the search left the tips.
    self.assertEqual(self.driver.head96_dispensing_drive_uL, 10.0)
    self.assertEqual((fields["blow_out_air_volume"], fields["lld_mode"]), (0, 0))
    self.assertAlmostEqual(fields["liquid_surface_no_lld"], (bottom + height) * 10, delta=1)
    self.assertEqual(fields["minimum_traverse_height_start"], fields["liquid_surface_no_lld"])
    self.assertEqual(fields["minimum_height"], round(bottom * 10))
    # 2 mm under it, following it down by what 96 draws of 20 uL take from the trough.
    self.assertEqual(fields["immersion_depth"], 20)
    drop = height - self.trough.compute_height_from_volume(100_000.0 - 96 * 20.0)
    self.assertAlmostEqual(fields["surface_following_distance"], drop * 10, delta=1)
    # Set to what the search measured, to the Z drive's resolution, then 96 draws booked.
    self.assertAlmostEqual(self.trough.tracker.get_used_volume(), 100_000.0 - 96 * 20.0, delta=50)
    self.assertEqual(self.head.piston_position, 30.0)

  async def test_capacitive_immersion_and_following_stop_at_the_floor(self):
    self.trough.tracker.set_volume(2_000.0)
    await self.head.aspirate(
      self.trough, piston_volume=20.0, lld_mode=LLDMode.CAPACITIVE, auto_surface_following=True
    )
    fields = self.sent[0][1]
    lowest = (
      fields["liquid_surface_no_lld"]
      - fields["immersion_depth"]
      - fields["surface_following_distance"]
    )
    self.assertGreaterEqual(lowest, fields["minimum_height"])

  async def test_capacitive_follows_nothing_unless_asked(self):
    self.trough.tracker.set_volume(100_000.0)
    await self.head.aspirate(self.trough, piston_volume=20.0, lld_mode=LLDMode.CAPACITIVE)
    self.assertEqual(self.sent[0][1]["surface_following_distance"], 0)

  async def test_auto_surface_following_searches_capacitive_when_no_mode_is_given(self):
    self.trough.tracker.set_volume(100_000.0)
    await self.head.aspirate(self.trough, piston_volume=20.0, auto_surface_following=True)
    fields = self.sent[0][1]
    height = self.trough.compute_height_from_volume(100_000.0)
    drop = height - self.trough.compute_height_from_volume(100_000.0 - 96 * 20.0)
    self.assertAlmostEqual(fields["surface_following_distance"], drop * 10, delta=1)

  async def test_auto_surface_following_refused_beside_a_distance_or_without_a_surface(self):
    refused: List[Dict[str, Any]] = [
      {"surface_following_distance": 1.0},
      {"lld_mode": LLDMode.OFF},
    ]
    for kwargs in refused:
      for call in (self.head.aspirate, self.head.dispense):
        with self.subTest(call=call.__name__, **kwargs), self.assertRaises(ValueError):
          await call(self.trough, piston_volume=20.0, auto_surface_following=True, **kwargs)
    self.assertEqual(self.sent, [])

  async def test_auto_surface_following_under_off_follows_from_the_liquid_height(self):
    self.trough.tracker.set_volume(100_000.0)
    await self.head.aspirate(
      self.trough,
      piston_volume=20.0,
      lld_mode=LLDMode.OFF,
      liquid_height=10.0,
      auto_surface_following=True,
    )
    held = self.trough.compute_volume_from_height(10.0)
    drop = 10.0 - self.trough.compute_height_from_volume(held - 96 * 20.0)
    self.assertAlmostEqual(self.sent[0][1]["surface_following_distance"], drop * 10, delta=1)

  async def test_a_pre_mix_auto_follows_what_one_draw_takes_from_the_surface_found(self):
    self.trough.tracker.set_volume(100_000.0)
    pre_mix = Mix(volume=10.0, repetitions=2, flow_rate=100.0, auto_surface_following=True)
    await self.head.aspirate(
      self.trough, piston_volume=20.0, lld_mode=LLDMode.CAPACITIVE, pre_mix=pre_mix
    )
    fields = self.sent[0][1]
    height = self.trough.compute_height_from_volume(100_000.0)
    drop = height - self.trough.compute_height_from_volume(100_000.0 - 96 * 10.0)
    self.assertAlmostEqual(fields["mix_surface_following_distance"], drop * 10, delta=1)
    self.assertEqual(fields["surface_following_distance"], 0)
    with self.assertRaises(ValueError):
      await self.head.aspirate(self.trough, piston_volume=20.0, pre_mix=pre_mix)

  async def test_capacitive_dispense_into_a_plate_books_each_well(self):
    await self.head.aspirate(self.plate, piston_volume=30.0)
    await self.head.dispense(
      self.plate, piston_volume=30.0, lld_mode=LLDMode.CAPACITIVE, auto_surface_following=True
    )
    fields = self.sent[1][1]
    well = self.plate.get_item("A1")
    bottom = well.get_location_wrt(self.deck, "c", "c", "cavity_bottom").z
    surface = bottom + well.compute_height_from_volume(170.0)
    self.assertAlmostEqual(fields["liquid_surface_no_lld"], surface * 10, delta=1)
    self.assertEqual(fields["minimum_traverse_height_start"], fields["liquid_surface_no_lld"])
    # Following up by what 30 uL raises the well's surface.
    rise = well.compute_height_from_volume(200.0) - well.compute_height_from_volume(170.0)
    self.assertAlmostEqual(fields["surface_following_distance"], rise * 10, delta=1)
    self.assertEqual(self.tip_volumes(), {0.0})
    others = {w.tracker.get_used_volume() for w in self.plate.get_all_items()[1:]}
    self.assertEqual(others, {200.0})

  async def test_no_liquid_under_capacitive_raises_and_the_head_goes_up(self):
    with self.assertRaises(RuntimeError):
      await self.head.aspirate(self.trough, piston_volume=20.0, lld_mode=LLDMode.CAPACITIVE)
    self.assertEqual(self.sent, [])
    self.assertEqual(await self.head.request_z_position(), self.head.configuration.z_range[1])

  async def test_a_failed_command_rolls_back_and_raises_the_head(self):
    self.failing = True
    with self.assertRaises(STARFirmwareError):
      await self.head.aspirate(self.plate, piston_volume=50.0)
    self.assertEqual(self.well_volumes(), {200.0})
    self.assertEqual(self.tip_volumes(), {0.0})
    self.assertEqual(self.head.piston_position, 0.0)
    self.assertEqual(await self.head.request_z_position(), self.head.configuration.z_range[1])

  async def test_what_a_failed_command_moved_is_booked_from_the_piston(self):
    self.failing = True
    self.moved_before_failure = 20.0
    with self.assertRaises(STARFirmwareError):
      await self.head.aspirate(self.plate, piston_volume=50.0)
    self.assertEqual(self.well_volumes(), {180.0})
    self.assertEqual(self.tip_volumes(), {20.0})

  async def test_refusals_send_nothing_and_move_nothing(self):
    z = await self.head.request_z_position()
    refusals = {
      "pressure": lambda: self.head.aspirate(self.plate, 5.0, lld_mode=LLDMode.PRESSURE),
      "both volumes": lambda: self.head.aspirate(self.plate, 5.0, piston_volume=5.0),
      "a height under cLLD": lambda: self.head.aspirate(
        self.plate, 5.0, liquid_height=2.0, lld_mode=LLDMode.CAPACITIVE
      ),
      "eight wells": lambda: self.head.aspirate(self.plate.get_all_items()[:8], 5.0),
      "one well of many": lambda: self.head.aspirate(self.plate.get_item("B1"), 5.0),
      "a fast flow": lambda: self.head.aspirate(self.plate, piston_volume=5.0, flow_rate=900.0),
      "past the piston": lambda: self.head.aspirate(self.plate, piston_volume=5_000.0),
      "an empty piston": lambda: self.head.dispense(self.plate, piston_volume=5.0),
      "z touch": lambda: self.head.dispense(self.plate, 5.0, lld_mode=LLDMode.ZTOUCH),
    }
    for name, refused in refusals.items():
      with self.subTest(name):
        with self.assertRaises(ValueError):
          await refused()
    self.assertEqual(self.sent, [])
    self.assertEqual(await self.head.request_z_position(), z)

  async def test_a_trough_without_room_is_refused(self):
    await self.head.aspirate(self.plate, piston_volume=50.0)
    self.trough.tracker.set_volume(self.trough.max_volume - 96 * 10.0)
    with self.assertRaises(ValueError):
      await self.head.dispense(self.trough, piston_volume=50.0)
    self.assertEqual(len(self.sent), 1)


# What a real 384-head answered, keyed by the module and command that asked. `er00/00` is the
# master's own "no error" prefix, which the reads that go through it carry and the ones addressed
# to the head's own module do not.
HEAD384_REPLIES = {
  "D0QW": "D0QWid0001qw1",
  "D0QG": "D0QGid0001qg2",
  "D0RF": "D0RFid0001rf1.4S b 2015-10-07",
  "C0QK": "C0QKid0001er00/00qk1",
  "C0QJ": "C0QJid0001er00/00xs01157xd0yk3402je2450",
}

# The same head type read off the master, which answers `C0 QY` with what the head answers `D0 QG`
# with. This driver asks the head, and this is what the two are checked to agree on.
HEAD384_TYPE_FROM_THE_MASTER = "C0QYid0001er00/00qy2"


async def head384() -> Tuple[Head384, List[str]]:
  """The 384-head of a simulated device, and the list its commands are recorded in.

  Returns:
    The feature, and the list every command it sends is appended to.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR_HEAD384)
  await driver.setup()
  head = cast(Head384, driver.head384)

  sent: List[str] = []
  answer = driver.send_command

  async def recorded(
    module: str,
    command: str,
    fmt: Optional[Any] = None,
    subsystem: Optional[str] = None,
    read_timeout: Optional[int] = None,
    **kwargs: Any,
  ):
    # `fmt`, `subsystem` and `read_timeout` are the driver's own, not firmware parameters, so they
    # never reach the assembler.
    sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
    return await answer(
      module=module,
      command=command,
      fmt=fmt,
      subsystem=subsystem,
      read_timeout=read_timeout,
      **kwargs,
    )

  driver.send_command = recorded  # type: ignore[assignment]
  return head, sent


async def head384_answering_captures() -> Head384:
  """A 384-head whose every read is answered with what a real one answered.

  On a driver of its own rather than the simulated one, which answers the reads from its model and
  would stand between these replies and the decoding they are here to check. Nothing is sent, so
  the driver needs no link.

  Returns:
    The feature.
  """
  driver = STARDriver(io=AsyncMock())

  async def answering(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
    reply = HEAD384_REPLIES[f"{module}{command}"]
    # As `_send` finishes: parsed against the format the caller gave, raw where it gave none.
    return driver._parse_response(reply, fmt) if fmt is not None else reply

  driver._send = answering  # type: ignore[assignment]
  return Head384(driver)


class TestHead384Commands(unittest.IsolatedAsyncioTestCase):
  """What the 384-head is sent, against what a real one was sent."""

  async def test_initialization_ejects_where_it_is_pointed(self):
    """`C0 JI`. The `xd1` is the sign of the X position: this device's waste sits left of the
    deck origin, and the head reports the position back the same way."""
    head, sent = await head384()
    await head.initialize(
      tip_discard_location=Coordinate(-161.3, 116.9, 242.0),
      z_position_at_the_command_end=245.0,
    )
    self.assertEqual(sent[0], "C0JIxs01613xd1yk1169je2420zg2450")

  async def test_the_retract_is_this_heads_own(self):
    """`C0 JV`, where the 96-head's is `C0 EV`: the head states which, and the probe sends it."""
    head, sent = await head384()
    await head.probe_z_max()
    self.assertEqual(sent[0], "C0JV")

  async def test_an_all_axis_move_is_addressed_to_this_head(self):
    """`C0 EN`, the 384-head's version of the 96-head's `C0 EM`."""
    head, sent = await head384()
    await head._unchecked_fw_move_to_coordinate(
      Coordinate(115.7, 340.2, 245.0),
      minimum_height_at_beginning_of_a_command=245.0,
    )
    self.assertEqual(sent, ["C0ENxs01157xd0yk3402je2450zf2450"])

  async def test_an_all_axis_move_travels_at_what_the_command_accepts(self):
    """Named no height, the move travels at the top of the command's own field, which is lower
    than the top of the Z drive's window."""
    head, sent = await head384()
    await head._unchecked_fw_move_to_coordinate(Coordinate(36.5, 116.9, 320.0))
    self.assertEqual(sent, ["C0ENxs00365xd0yk1169je3200zf3270"])

  async def test_a_y_only_move_carries_y_and_a_height_and_nothing_else(self):
    """`C0 EY`: the master raises the head to the height given and then travels."""
    head, sent = await head384()
    await head._unchecked_fw_safety_move_to_y_position(
      244.2, minimum_height_at_beginning_of_a_command=320.0
    )
    self.assertEqual(sent, ["C0EYyk2442zf3200"])

  async def test_it_picks_up_a_rack_of_tips(self):
    """`C0 JB`, sent as the firmware takes it."""
    head, sent = await head384()
    await head._unchecked_fw_pick_up_tips(
      x_position=1157,
      x_direction=0,
      y_position=2442,
      tip_type_table_index=33,
      z_pick_up_position=2195,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
      centering=False,
    )
    self.assertEqual(sent, ["C0JBxs01157xd0yk2442tt33iu0je2195zf2450zg2450ii0"])

  async def test_it_discards_tips_to_a_rack_and_to_the_waste(self):
    """`C0 JC` twice: returning a rack, and ejecting where `C0 JI` ejects."""
    head, sent = await head384()
    await head._unchecked_fw_discard_tips(
      x_position=1157,
      x_direction=0,
      y_position=2442,
      z_deposit_position=2195,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
    )
    await head._unchecked_fw_discard_tips(
      x_position=1613,
      x_direction=1,
      y_position=1169,
      z_deposit_position=2420,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
    )
    self.assertEqual(
      sent,
      [
        "C0JCxs01157xd0yk2442je2195zf2450zg2450jd0",
        "C0JCxs01613xd1yk1169je2420zf2450zg2450jd0",
      ],
    )

  async def test_it_aspirates(self):
    """`C0 JA`, which leaves out `ig` and `ih` where no capacitive LLD is asked for."""
    head, sent = await head384()
    await head._unchecked_fw_aspirate(
      x_position=2507,
      x_direction=0,
      y_position=3402,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
      lld_search_height=2231,
      liquid_surface_no_lld=1901,
      minimum_height=1861,
      aspiration_volume=4800,
      aspiration_speed=500,
      transport_air_volume=300,
      blow_out_air_volume=300,
      lld_mode=0,
      swap_speed=20,
      settling_time=10,
      homogenization_speed=500,
      pull_out_distance_transport_air=100,
    )
    self.assertEqual(
      sent,
      [
        "C0JAja0xs02507xd0yk3402zf2450zg2450jz2231jt1901jm1861jw000jx0jh000jf04800"
        "jg0500ju0300jv00300jy00000jq0jp1js0020ji10jj00000jk00jl000jn0500zw0000zs00000"
        "mk000pq0100"
      ],
    )

  async def test_it_dispenses(self):
    """`C0 JD`, which leaves out `ig` and `ih` as the aspiration does."""
    head, sent = await head384()
    await head._unchecked_fw_dispense(
      dispensing_mode=1,
      x_position=2507,
      x_direction=0,
      y_position=1482,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
      lld_search_height=2001,
      liquid_surface_no_lld=1876,
      minimum_height=1871,
      dispense_volume=4800,
      dispense_speed=500,
      cut_off_speed=200,
      transport_air_volume=300,
      blow_out_air_volume=300,
      lld_mode=0,
      swap_speed=20,
      settling_time=5,
      mix_speed=500,
      pull_out_distance_transport_air=100,
    )
    self.assertEqual(
      sent,
      [
        "C0JDjo1xs02507xd0yk1482jm1871jz2001jt1876jw000jx0jh000zf2450zg2450jb04800"
        "jc0500jr0200im0000ju0300jv00300jq0jp1js0020ji05jj00000jk00jl000jn0500zw0000ij00"
        "zs00000mk000pq0100"
      ],
    )

  async def test_the_capacitive_lld_gain_and_offset_are_appended_when_asked_for(self):
    """`ig` and `ih` go on the end of the aspiration and the dispense, in that order, and are
    left out where the caller names neither."""
    head, sent = await head384()
    where = dict(
      x_position=2507,
      x_direction=0,
      y_position=3402,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      minimum_height_at_command_end=2450,
      liquid_surface_no_lld=1901,
      minimum_height=1861,
    )
    await head._unchecked_fw_aspirate(aspiration_volume=4800, **where)
    await head._unchecked_fw_aspirate(
      aspiration_volume=4800, capacitive_lld_gain=512, capacitive_lld_offset=64, **where
    )
    await head._unchecked_fw_dispense(
      dispense_volume=4800, capacitive_lld_gain=512, capacitive_lld_offset=64, **where
    )
    self.assertFalse(sent[0].endswith("ig0512ih0064"))
    self.assertTrue(sent[1].endswith("ig0512ih0064"))
    self.assertTrue(sent[2].endswith("ig0512ih0064"))

  async def test_it_washes_and_empties_the_tips(self):
    """`C0 JG` and `C0 JU`. Field widths only: no wash station was fitted to pin them against."""
    head, sent = await head384()
    await head._unchecked_fw_wash_tips(
      x_position=1157,
      x_direction=0,
      y_position=2442,
      wash_z_position=1800,
      minimum_height=1700,
      minimum_traverse_height_at_beginning_of_a_command=2450,
      wash_volume=5000,
      wash_cycles=3,
    )
    await head._unchecked_fw_empty_washed_tips(z_position=2450, minimum_height_at_command_end=2450)
    self.assertEqual(
      sent,
      [
        "C0JGxs01157xd0yk2442jt1800jm1700jh000zf2450jj05000jk03jn2000",
        "C0JUjt2450zg2450",
      ],
    )

  async def test_each_head_names_its_own_command_and_parameters(self):
    """The four constants the all-axis move turns on, which is why they are configuration. A head
    given the other's fails here before any test that would send one."""
    named = {
      name: (c.defined_position_command, c.y_parameter, c.z_parameter, c.traverse_z_parameter)
      for name, c in (("96", Head96Configuration()), ("384", Head384Configuration()))
    }
    self.assertEqual(named, {"96": ("EM", "yh", "za", "zh"), "384": ("EN", "yk", "je", "zf")})


class TestHead384Queries(unittest.IsolatedAsyncioTestCase):
  """What the 384-head answers, against what a real one replied."""

  async def test_it_reports_itself_initialized(self):
    """`D0 QW`, asked of the head's own module: the master's command table does not carry it."""
    head = await head384_answering_captures()
    self.assertTrue(await head._driver.request_initialization_status(module="D0"))

  async def test_it_reports_tips_mounted(self):
    """`C0 QK`, where the 96-head's is `C0 QH`."""
    head = await head384_answering_captures()
    self.assertTrue(await head.request_tip_presence())

  async def test_it_reports_where_channel_a1_is(self):
    """`C0 QJ`, in the parameter names this head's commands use throughout: `yk` and `je`."""
    head = await head384_answering_captures()
    self.assertEqual(await head.request_location(), Coordinate(115.7, 340.2, 245.0))

  async def test_it_reports_which_head_is_fitted(self):
    """`D0 QG`, decoded through this head's own table: code 2 is the shifted tip pickup head,
    where the same code on a 96-head means a 96 head II. The master's `C0 QY` agrees."""
    head = await head384_answering_captures()
    self.assertEqual(await head.request_head_type(), "STP head")
    from_the_master = int(HEAD384_TYPE_FROM_THE_MASTER.split("qy")[-1])
    self.assertEqual(head.configuration.head_types[from_the_master], "STP head")

  async def test_it_reports_its_own_firmware(self):
    """`D0 RF`. The version is kept whole, date included, as every other feature keeps it."""
    head = await head384_answering_captures()
    self.assertEqual(
      await head.request_firmware_version(),
      ("1.4S b 2015-10-07", datetime.date(2015, 10, 7)),
    )


def tip_rack_384(name: str, num_items_x: int = 24, num_items_y: int = 16) -> TipRack:
  """A 384 tip rack to place a command against.

  Built here rather than taken from the resource library, which carries no Hamilton 384 rack: the
  geometry below is a plate footprint on the head's own 4.5 mm pitch, which is what a tip command
  needs to be placed, and is not a measurement of any particular rack.

  Args:
    name: what to call it.
    num_items_x: how many columns, for building a rack of the wrong size.
    num_items_y: how many rows.

  Returns:
    The rack, filled with tips.
  """
  return TipRack(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=20.0,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      dx=9.0,
      dy=6.8,
      dz=12.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.0,
      size_y=3.0,
      make_tip=hamilton_tip_50uL,
      name_prefix=name,
    ),
  )


class TestHead384Tips(unittest.IsolatedAsyncioTestCase):
  """Collecting and returning a rack on the 384-head, and what that does to the model."""

  async def asyncSetUp(self):
    from pylabrobot.resources import set_tip_tracking
    from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    self.deck = STARLetDeck()
    self.driver = STARSimulationDriver(
      deck=self.deck, declared_configuration_json=RECORDING_STARLET_HEAD384
    )
    await self.driver.setup()
    self.head = cast(Head384, self.driver.head384)
    self.head_resource = cast(NChannelPipette, self.head.resource)
    carrier = PLT_CAR_L5AC_A00(name="plate carrier")
    carrier[0] = self.tip_rack = tip_rack_384(name="tip_rack_384")
    self.deck.assign_child_resource(carrier, track=9)
    self.sent: List[str] = []
    log = self.driver._log_exchange

    def recorded(written: str, read: Optional[str]) -> None:
      if written[:4] in ("C0TT", "C0JB", "C0JC"):
        self.sent.append(written)
      log(written, read)

    self.driver._log_exchange = recorded  # type: ignore[method-assign]

  async def test_pick_up_and_drop_place_the_command_from_the_deck(self):
    await self.head.pick_up_tips(self.tip_rack)
    await self.head.drop_tips(self.tip_rack)
    await self.head.drop_tips(self.deck.get_trash_area96())
    self.assertEqual(
      self.sent,
      [
        "C0TTtt01tf0tl0424tv00650tg2tu0",
        "C0JBxs02945xd0yk1473tt01iu0je1982zf2450zg2450ii1",
        "C0JCxs02945xd0yk1473je1982zf2450zg2450jd0",
        "C0JCxs00488xd1yk1810je2164zf2450zg2450jd0",
      ],
    )

  async def test_the_rack_empties_onto_the_head_and_fills_again(self):
    spots = self.tip_rack.get_all_items()
    shafts = self.head_resource.get_all_items()
    tips = [spot.tip for spot in spots]

    await self.head.pick_up_tips(self.tip_rack)
    self.assertEqual([shaft.tip for shaft in shafts], tips)
    self.assertFalse(any(spot.has_tip() for spot in spots))

    await self.head.drop_tips(self.tip_rack)
    self.assertEqual([spot.tip for spot in spots], tips)
    self.assertFalse(any(shaft.has_tip() for shaft in shafts))

  async def test_tips_dropped_in_the_trash_belong_to_nothing(self):
    await self.head.pick_up_tips(self.tip_rack)
    tips = [shaft.tip for shaft in self.head_resource.get_all_items()]
    await self.head.drop_tips(self.deck.get_trash_area96())
    self.assertFalse(any(shaft.has_tip() for shaft in self.head_resource.get_all_items()))
    self.assertTrue(all(tip is not None and tip.parent is None for tip in tips))

  async def test_a_rack_of_the_wrong_size_is_refused_before_anything_is_sent(self):
    wrong = tip_rack_384(name="tip_rack_96", num_items_x=12, num_items_y=8)
    self.deck.assign_child_resource(wrong, location=Coordinate(700.0, 200.0, 100.0))
    with self.assertRaises(ValueError):
      await self.head.pick_up_tips(wrong)
    with self.assertRaises(ValueError):
      await self.head.drop_tips(wrong)
    self.assertEqual(self.sent, [])
