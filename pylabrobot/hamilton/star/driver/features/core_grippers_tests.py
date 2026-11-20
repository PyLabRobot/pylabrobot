import asyncio
import dataclasses
import re
import unittest
import unittest.mock
from typing import Any, Dict, List

from pylabrobot.hamilton.protocol.text.framing import assemble_command, parse_fw_string
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import STAR
from pylabrobot.hamilton.star.driver.errors import check_fw_string_error
from pylabrobot.hamilton.star.driver.features.x_arm_tests import RECORDED_DEVICE, declaring
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.alpaqua.magnetic_racks import alpaqua_96_plateadapter_magnum_flx
from pylabrobot.resources.azenta.plates import azenta_96_wellplate_200uL_Vb_4titudeframestar
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import cor_96_wellplate_2mL_Vb
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00, STARDeck, hamilton_tip_300uL
from pylabrobot.resources.hamilton.mfx_carriers import hamilton_mfx_carrier_L5_base
from pylabrobot.resources.hamilton.mfx_modules import (
  hamilton_mfx_plateholder_DWP_flat,
  hamilton_mfx_plateholder_DWP_metal_tapped,
)
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash


class TestWiring(unittest.IsolatedAsyncioTestCase):
  async def test_built_on_the_arm_that_carries_pipettes(self):
    star = STAR(simulation=True)
    await star.setup()
    grippers = star.driver.x_arm.core_grippers
    self.assertIsNotNone(grippers)
    self.assertIs(star.driver.core_grippers, grippers)
    self.assertIs(star.core_grippers, grippers)

  async def test_none_on_an_arm_without_pipettes(self):
    both = dataclasses.replace(RECORDED_DEVICE, right_arm=BARE_X_ARM)
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(device=both)
    )
    await driver.setup()
    assert driver.left_x_arm is not None and driver.right_x_arm is not None
    self.assertIsNotNone(driver.left_x_arm.core_grippers)
    self.assertIsNone(driver.right_x_arm.core_grippers)
    with self.assertRaises(ValueError):
      driver.core_grippers

  async def test_kept_across_a_second_setup(self):
    star = STAR(simulation=True)
    await star.setup()
    grippers = star.core_grippers
    await star.setup()
    self.assertIs(star.core_grippers, grippers)

  async def test_carried_by_the_arms_pipettes(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.assertIs(star.core_grippers.arm, star.driver.x_arm)
    self.assertIs(star.core_grippers._pipettes, star.driver.x_arm.pipettes)


class TestState(unittest.IsolatedAsyncioTestCase):
  async def test_not_mounted_refuses(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.assertFalse(star.core_grippers.tools_mounted)
    with self.assertRaises(RuntimeError):
      star.core_grippers._require_mounted()


class TestToolFirmware(unittest.IsolatedAsyncioTestCase):
  """`C0 ZT` and `C0 ZS` as they go on the wire, byte for byte as legacy sends them."""

  async def asyncSetUp(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.grippers = star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    # The simulator reads the channels back as it records a command, so those reads are answered.
    async def capturing(module: str, command: str, **kwargs: Any):
      if module == "C0" and command.startswith("Z"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  async def test_pick_up(self):
    await self.grippers._unchecked_fw_pick_up_tools(13375, 1250, 1070, 2350, 2250, 2800, 6, 7, 14)
    self.assertEqual(self.sent, ["C0ZTxs13375xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"])

  async def test_drop(self):
    await self.grippers._unchecked_fw_drop_tools(13375, 1250, 1070, 2150, 2050, 2800, 2800)
    self.assertEqual(self.sent, ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"])

  async def test_negative_x_sends_the_direction(self):
    await self.grippers._unchecked_fw_pick_up_tools(-120, 1250, 1070, 2350, 2250, 2800, 4, 5, 14)
    self.assertEqual(self.sent, ["C0ZTxs00120xd1ya1250yb1070pa05pb06tp2350tz2250th2800tt14"])

  async def test_put_down_move_and_release_as_legacy(self):
    await self.grippers._unchecked_fw_place_resource(8204, 2102, 1954, 0, 500, 885, 2800, 2800)
    await self.grippers._unchecked_fw_move_resource(8204, 4, 2102, 2500, 500, 2800)
    await self.grippers._unchecked_fw_release_plate()
    self.assertEqual(
      self.sent,
      [
        "C0ZRxs08204xd0yj2102zj1954zi000zy0500yo0885th2800te2800",
        "C0ZMxs08204xd0xg4yj2102zj2500zy0500th2800",
        "C0ZO",
      ],
    )

  async def test_put_down_with_an_x_acceleration(self):
    await self.grippers._unchecked_fw_place_resource(
      8204, 2102, 1954, 0, 500, 885, 2800, 2800, x_acceleration_level=2
    )
    self.assertEqual(self.sent, ["C0ZRxs08204xd0xg2yj2102zj1954zi000zy0500yo0885th2800te2800"])


class TestPickUpAndDropTools(unittest.IsolatedAsyncioTestCase):
  """The checked tool pick-up and drop: what they refuse, send and leave behind."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2 and not isinstance(v, list)}
      self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  def tool_commands(self) -> List[str]:
    return [c for c in self.sent if c[2:4] in ("ZT", "ZS")]

  def safe_z_moves(self) -> List[str]:
    return [c[:4] for c in self.sent if c[:4] == "C0ZA" or (c[0] == "P" and c[2:4] == "ZA")]

  async def test_default_pair_sends_what_legacy_sends(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(
      self.tool_commands(), ["C0ZTxs13375xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"]
    )
    self.assertEqual(self.safe_z_moves(), [])

  async def test_the_tools_on_by_location_alone_let_the_grippers_act(self):
    with self.assertRaises(RuntimeError):
      self.grippers._require_mounted()
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.grippers._require_mounted(), (6, 7))
    self.assertFalse(self.grippers.tools_mounted)

  async def test_a_named_pair(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0, front_channel=5)
    self.assertIn("pa05pb06", self.tool_commands()[0])

  async def test_refused_before_anything_is_sent(self):
    for kwargs in ({"front_channel": 0}, {"front_channel": 8}):
      with self.assertRaises(ValueError):
        await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0, **kwargs)
    with self.assertRaises(ValueError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 125.0, 107.0)
    with self.assertRaises(ValueError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0, tool_seek=220.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_a_channel_carrying_something_is_refused(self):
    shaft = self.grippers._pipettes.shaft(7)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="on_channel_7"))
    with self.assertRaises(HasTipError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_iswap_not_parked_is_refused(self):
    iswap = self.star.driver.x_arm.iswap
    assert iswap is not None

    async def not_parked(*args: Any, **kwargs: Any) -> bool:
      return False

    iswap.request_is_parked = not_parked  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_drop_goes_back_20_mm_lower_as_legacy(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.sent.clear()
    await self.grippers.drop_tools()
    self.assertEqual(self.tool_commands(), ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"])
    self.assertEqual(self.safe_z_moves(), ["C0ZA"])
    with self.assertRaises(RuntimeError):
      await self.grippers.drop_tools()

  async def test_a_second_pick_up_is_refused(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)

  async def test_a_failed_pick_up_still_goes_to_safe_z(self):
    async def refused(*args: Any, **kwargs: Any):
      raise RuntimeError("the device")

    self.grippers._unchecked_fw_pick_up_tools = refused  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.safe_z_moves(), ["C0ZA"])
    self.assertIsNone(self.grippers._tools_taken_from)


class TestCheckResourceExists(unittest.IsolatedAsyncioTestCase):
  """Both tools z-touch down together where the open jaws would land on the plate."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.star.deck.assign_child_resource(carrier, track=30)
    carrier[0] = self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.location = self.plate.get_location_wrt(self.star.deck)
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    # Channels with a z-touch in flight; whether two ever were at once.
    self.searching: List[str] = []
    self.searched_together = False

    async def capturing(module: str, command: str, **kwargs: Any):
      if command in ("XP", "JY", "ZH", "JZ", "ZA", "ZP"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      if command != "ZH":
        return await answer(module=module, command=command, **kwargs)
      self.searching.append(module)
      await asyncio.sleep(0)
      self.searched_together |= len(self.searching) > 1
      try:
        return await answer(module=module, command=command, **kwargs)
      finally:
        self.searching.remove(module)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  async def check(self, **kwargs: Any) -> bool:
    return await self.grippers.probe_resource_exists_using_ztouch(
      location=self.location, resource=self.plate, **kwargs
    )

  async def pick_up_tools(self) -> None:
    await self.grippers.pick_up_tools()
    self.sent.clear()

  async def test_refused_without_the_tools(self):
    with self.assertRaises(RuntimeError):
      await self.check(gripper_y_margin=9, enable_recovery=False)

  async def test_nothing_there_touches_with_both_tools_to_the_centre_and_is_false(self):
    await self.pick_up_tools()
    self.plate.unassign()
    heights = {"minimum_traverse_height_start": 275.0, "minimum_traverse_height_end": 275.0}
    self.assertFalse(await self.check(gripper_y_margin=9, enable_recovery=False, **heights))
    # Over the centre, 9 mm inside each wall; the grip line from 275 mm to the centre's 193.4 mm.
    self.assertEqual(
      self.sent,
      [
        "X0XPla08204lr3lw7",
        "C0JYyp4050 3783 3516 3249 2982 2715 1480 0805",
        "P7ZHzb27685za20079zv11652zr019zu03729cg015cf000",
        "P8ZHzb27685za20079zv11652zr019zu03729cg015cf000",
        "C0JZzp3347 3347 3347 3347 3347 3347 2750 2750",
      ],
    )

  async def test_nothing_there_answered_with_error_63_at_the_end_is_false(self):
    await self.pick_up_tools()
    self.plate.unassign()
    end = 20079
    # As a device answered over an empty site: error 63, then each channel 22 and 19 increments
    # past the end.
    replies = {
      "P7ZH": "P7ZHid0251er63rz+00000",
      "P8ZH": "P8ZHid0252er63rz+00000",
      "P7RZ": f"P7RZid0253rz+{end - 22:05} +{end - 21:05}",
      "P8RZ": f"P8RZid0254rz+{end - 19:05} +{end - 19:05}",
    }
    searched = set()
    capturing = self.grippers._driver.send_command

    async def answering(module: str, command: str, **kwargs: Any):
      reply = replies.get(module + command)
      if reply is None or (command == "RZ" and module not in searched):
        return await capturing(module=module, command=command, **kwargs)
      if command == "ZH":
        searched.add(module)
        await capturing(module=module, command=command, **kwargs)
        check_fw_string_error(reply)
      return parse_fw_string(reply, kwargs["fmt"])

    self.grippers._driver.send_command = answering  # type: ignore[assignment]
    logger = "pylabrobot.hamilton.star.driver.features.core_grippers"
    with self.assertLogs(logger, level="INFO") as logs:
      self.assertFalse(await self.check(gripper_y_margin=9, enable_recovery=False))
    self.assertIn("'plate' not found", logs.output[-1])
    self.assertEqual([command[11:18] for command in self.sent if "ZH" in command], [f"za{end}"] * 2)

  async def test_the_heights_default_to_the_traverse_height(self):
    await self.pick_up_tools()
    self.plate.unassign()
    await self.check(gripper_y_margin=9, enable_recovery=False)
    # The search starts at 280 mm and the channels end there.
    touches = [command[4:11] for command in self.sent if "ZH" in command]
    self.assertEqual(touches, ["zb28152", "zb28152"])
    self.assertTrue(self.sent[-1].endswith("2800 2800"), self.sent[-1])

  async def test_a_plate_there_is_met_by_both_tools_and_found(self):
    await self.pick_up_tools()
    self.assertTrue(await self.check(gripper_y_margin=9, enable_recovery=False))
    self.assertEqual([command[:4] for command in self.sent if "ZH" in command], ["P7ZH", "P8ZH"])
    self.assertTrue(self.searched_together)
    self.assertFalse([command for command in self.sent if command.startswith("C0ZP")])
    self.assertTrue(self.sent[-1].endswith("2800 2800"), self.sent[-1])

  async def test_one_tool_meeting_something_is_found(self):
    await self.pick_up_tools()
    site = self.plate.parent
    assert site is not None
    self.plate.unassign()
    # A post under the back tool only, which lands at y 147.95, 9 mm inside the back wall.
    post = Resource(name="post", size_x=10, size_y=10, size_z=20)
    corner = Coordinate(820.4 - 5, 147.95 - 5, 0) - site.get_location_wrt(self.star.deck)
    site.assign_child_resource(post, location=Coordinate(corner.x, corner.y, 0))
    logger = "pylabrobot.hamilton.star.driver.features.core_grippers"
    with self.assertLogs(logger, level="INFO") as logs:
      self.assertTrue(await self.check(gripper_y_margin=9, enable_recovery=False))
    met = re.search(
      r"the back tool met it at ([\d.]+) mm, the front tool met nothing", logs.output[-1]
    )
    assert met is not None, logs.output[-1]
    top = post.get_location_wrt(self.star.deck, z="t").z
    self.assertAlmostEqual(float(met.group(1)), top, delta=0.05)

  async def test_a_failed_search_waits_for_the_other_goes_to_safe_z_and_raises(self):
    await self.pick_up_tools()
    pipettes = self.grippers._pipettes
    search = pipettes._ztouch_search

    async def front_fails(channel: int, **kwargs: Any) -> float:
      if channel == self.grippers._front_channel:
        raise RuntimeError("the front search")
      return await search(channel=channel, **kwargs)

    pipettes._ztouch_search = front_fails  # type: ignore[method-assign, assignment]
    with self.assertRaises(RuntimeError):
      await self.check(gripper_y_margin=9, enable_recovery=False)
    self.assertEqual(
      [command for command in self.sent if "ZH" in command or "ZA" in command][-2:],
      ["P7ZHzb28152za20079zv11652zr019zu03729cg015cf000", "C0ZA"],
    )

  async def test_recovery_checks_again_on_yes_and_aborts_on_abort(self):
    await self.pick_up_tools()
    self.plate.unassign()
    answers = unittest.mock.Mock(side_effect=["yes", "abort"])
    with unittest.mock.patch("builtins.input", answers), unittest.mock.patch("builtins.print"):
      with self.assertRaises(ValueError):
        await self.check(gripper_y_margin=9, enable_recovery=True)
    self.assertEqual(answers.call_count, 2)
    touches = [command[:4] for command in self.sent if "ZH" in command]
    self.assertEqual(touches, ["P7ZH", "P8ZH", "P7ZH", "P8ZH"])

  async def test_a_width_out_of_range_sends_nothing(self):
    await self.pick_up_tools()
    with self.assertRaises(ValueError):
      await self.check(gripper_y_margin=40, enable_recovery=False)
    self.assertEqual(self.sent, [])


class TestResourceByResource(unittest.IsolatedAsyncioTestCase):
  """`pick_up_resource`, `place_resource` and `return_resource`: the wire and the tree."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    self.carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.star.deck.assign_child_resource(self.carrier, track=30)
    self.carrier[0] = self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      if command in ("ZP", "ZR"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  async def test_refused_without_the_tools(self):
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    self.assertEqual(self.sent, [])

  async def test_pick_up_sends_what_legacy_sends_and_hangs_it_on_the_front_tool(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    # Legacy: the centre, 5 mm below the top, open to its width + 3.0 mm and closed to it - 3.0 mm.
    self.assertEqual(
      self.sent, ["C0ZPxs08204xd0yj1142yv0050zj1964zy0500yo0885yg0825yw15th2800te2800"]
    )
    self.assertIs(self.plate.parent, self.grippers._front_tool())

  async def test_place_into_a_site_sends_the_put_down_and_the_tree_follows(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    await self.grippers.place_resource(
      self.carrier[2], press_on_distance=1.5, x_acceleration_level=2
    )
    self.assertEqual(self.sent[1], "C0ZRxs08204xd0xg2yj3062zj1964zi015zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.carrier[2])
    self.assertIsNone(self.grippers._held_resource)

  async def test_a_coordinate_puts_its_centre_bottom_there_on_the_deck(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    await self.grippers.place_resource(Coordinate(400.0, 200.0, 100.0))
    # Let go 5 mm below its top: 100 + 16.1 - 5.
    self.assertEqual(self.sent[1], "C0ZRxs04000xd0yj2000zj1111zi000zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.star.deck)
    ccb = self.plate.get_location_wrt(self.star.deck, "c", "c", "b")
    self.assertEqual((ccb.x, ccb.y, ccb.z), (400.0, 200.0, 100.0))

  async def test_a_coordinate_inside_a_carrier_is_refused_before_anything_is_sent(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    site = self.carrier[2].get_location_wrt(self.star.deck, "c", "c", "b")
    with self.assertRaisesRegex(ValueError, "plate_carrier"):
      await self.grippers.place_resource(site)
    self.assertEqual(len(self.sent), 1)
    self.assertIs(self.grippers._held_resource, self.plate)

  async def test_an_occupied_site_is_refused_before_anything_is_sent(self):
    self.carrier[2] = other = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="other")
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    with self.assertRaises(RuntimeError):
      await self.grippers.place_resource(self.carrier[2])
    self.assertEqual(len(self.sent), 1)
    self.assertIs(self.grippers._held_resource, self.plate)
    self.assertIs(self.carrier[2].resource, other)

  async def test_return_puts_it_back_where_it_was_taken_from(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    await self.grippers.return_resource()
    self.assertEqual(self.sent[1], "C0ZRxs08204xd0yj1142zj1964zi000zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.carrier[0])

  async def test_place_without_holding_raises(self):
    await self.grippers.pick_up_tools()
    with self.assertRaises(RuntimeError):
      await self.grippers.place_resource(self.carrier[2])
    with self.assertRaises(RuntimeError):
      await self.grippers.return_resource()

  async def test_a_second_pick_up_while_holding_is_refused(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    self.assertEqual(len(self.sent), 1)

  async def test_gripped_more_than_20_mm_below_its_top_is_refused(self):
    self.carrier[1] = deep = cor_96_wellplate_2mL_Vb(name="deep")
    await self.grippers.pick_up_tools()
    with self.assertRaisesRegex(ValueError, "pipetting head"):
      await self.grippers.pick_up_resource(deep, pickup_distance_from_top=20.5)
    with self.assertRaisesRegex(ValueError, "pipetting head"):
      await self.grippers.pick_up_resource(
        deep, pickup_distance_from_bottom=deep.get_size_z() - 20.5
      )
    self.assertEqual(self.sent, [])
    await self.grippers.pick_up_resource(deep, pickup_distance_from_top=12.0)
    self.assertEqual(len(self.sent), 1)

  async def test_a_grip_from_the_bottom_sends_what_the_same_grip_from_the_top_sends(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    await self.grippers.return_resource()
    await self.grippers.pick_up_resource(
      self.plate, pickup_distance_from_bottom=self.plate.get_size_z() - 5
    )
    await self.grippers.return_resource()
    self.assertEqual(self.sent[2:], self.sent[:2])

  async def test_a_pick_up_with_neither_or_both_distances_is_refused(self):
    await self.grippers.pick_up_tools()
    for from_top, from_bottom in ((None, None), (5.0, 11.1)):
      with self.assertRaisesRegex(ValueError, "exactly one", msg=f"{from_top}, {from_bottom}"):
        await self.grippers.pick_up_resource(
          self.plate, pickup_distance_from_top=from_top, pickup_distance_from_bottom=from_bottom
        )
    self.assertEqual(self.sent, [])
    self.assertIsNone(self.grippers._held_resource)

  async def test_the_grip_line_is_checked_against_its_own_reach(self):
    await self.grippers.pick_up_tools()
    pipettes = self.grippers._pipettes
    low, high = pipettes.configuration.z_range
    # The tool's grip line is 22 mm below the stop disc and 2 mm above the tool's bottom.
    pipettes.configuration.z_range = (low, 196.4 + 22.0 - 0.5)
    with self.assertRaisesRegex(ValueError, "grip line reaches"):
      await self.grippers.pick_up_resource(
        self.plate,
        pickup_distance_from_top=5,
        minimum_traverse_height_start=210.0,
        minimum_traverse_height_end=210.0,
      )
    pipettes.configuration.z_range = (196.4 - 2.0 + 0.5, high)
    with self.assertRaisesRegex(ValueError, "grip line reaches"):
      await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    self.assertEqual(self.sent, [])
    pipettes.configuration.z_range = (low, high)
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    self.assertEqual(len(self.sent), 1)

  async def test_out_of_range_arguments_send_nothing(self):
    await self.grippers.pick_up_tools()
    for kwargs in ({"grip_strength": 100}, {"squeeze_mm": 50.0}, {"z_speed": 0.0}):
      with self.assertRaises(ValueError, msg=str(kwargs)):
        await self.grippers.pick_up_resource(self.plate, **kwargs)
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    for place_kwargs in ({"press_on_distance": 100.0}, {"x_acceleration_level": 6}):
      with self.assertRaises(ValueError, msg=str(place_kwargs)):
        await self.grippers.place_resource(self.carrier[2], **place_kwargs)
    self.assertEqual(len(self.sent), 1)


class TestPlaceOntoAPlateAdapter(unittest.IsolatedAsyncioTestCase):
  """`place_resource` onto a `PlateAdapter`: aimed and placed where the adapter places a plate."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    source = hamilton_mfx_plateholder_DWP_metal_tapped("source_site")
    self.star.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("source_carrier", modules={0: source}), track=10
    )
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    source.assign_child_resource(self.plate)
    magnet_site = hamilton_mfx_plateholder_DWP_flat("magnet_site")
    self.star.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("magnet_carrier", modules={0: magnet_site}), track=18
    )
    self.magnet = alpaqua_96_plateadapter_magnum_flx(name="magnet")
    self.magnet.plate_z_offset = 0.62
    magnet_site.assign_child_resource(self.magnet)
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      if command == "ZR":
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  async def test_the_plate_goes_where_the_adapter_places_it(self):
    await self.grippers.pick_up_tools(front_channel=4)
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    await self.grippers.place_resource(self.magnet)
    # Let go 6 mm below its top, the plate sitting 28.12 mm up in the magnet.
    self.assertEqual(self.sent, ["C0ZRxs05504xd0yj1133zj2162zi000zy0500yo0885th2800te2800"])
    self.assertIs(self.plate.parent, self.magnet)
    self.assertEqual(self.plate.location, self.magnet.compute_plate_location(self.plate))


class TestPlaceByDestinationType(unittest.IsolatedAsyncioTestCase):
  """`place_resource` into each kind of destination: the `C0 ZR` sent, and the tree."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    self.source = hamilton_mfx_plateholder_DWP_metal_tapped("source_site")
    self.star.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("source_carrier", modules={0: self.source}), track=10
    )
    self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    self.source.assign_child_resource(self.plate)
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      if command == "ZR":
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]
    await self.grippers.pick_up_tools(front_channel=4)

  async def test_a_rotated_holder_takes_it_turned_as_it_was(self):
    turned = ResourceHolder(
      "turned", size_x=127.76, size_y=85.48, size_z=5, rotation=Rotation(z=180)
    )
    self.star.deck.assign_child_resource(turned, location=Coordinate(600, 250, 100))
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    await self.grippers.place_resource(turned)
    self.assertEqual(self.sent, ["C0ZRxs05361xd0yj2073zj1101zi000zy0500yo0885th2800te2800"])
    self.assertIs(turned.resource, self.plate)
    self.assertEqual(self.plate.get_absolute_rotation().z % 360, 0)

  async def test_a_resource_stack_takes_it_on_top(self):
    bottom = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="bottom")
    stack = ResourceStack("stack", "z", [bottom])
    self.star.deck.assign_child_resource(stack, location=Coordinate(600, 150, 100))
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    await self.grippers.place_resource(stack)
    # Let go 6 mm below its top, on top of the 16.1 mm plate under it.
    self.assertEqual(self.sent, ["C0ZRxs06639xd0yj1927zj1262zi000zy0500yo0885th2800te2800"])
    self.assertIs(stack.get_top_item(), self.plate)

  async def test_a_lid_goes_onto_a_plate(self):
    size_x, size_y = self.plate.get_size_x(), self.plate.get_size_y()
    lid = Lid("lid", size_x=size_x, size_y=size_y, size_z=8, nesting_z_height=3)
    self.plate.assign_child_resource(lid)
    site = hamilton_mfx_plateholder_DWP_metal_tapped("dest_site")
    self.star.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("dest_carrier", modules={0: site}), track=18
    )
    other = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="other")
    site.assign_child_resource(other)
    await self.grippers.pick_up_resource(lid, pickup_distance_from_top=4, grip_strength=40)
    await self.grippers.place_resource(other)
    self.assertEqual(self.sent, ["C0ZRxs05504xd0yj1142zj2002zi000zy0500yo0885th2800te2800"])
    self.assertIs(other.lid, lid)
    self.assertIsNone(self.plate.lid)

  async def test_a_trash_discards_it(self):
    trash = Trash("bin", size_x=130, size_y=90, size_z=40)
    self.star.deck.assign_child_resource(trash, location=Coordinate(600, 150, 100))
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    await self.grippers.place_resource(trash)
    self.assertEqual(self.sent, ["C0ZRxs06639xd0yj1927zj1101zi000zy0500yo0885th2800te2800"])
    self.assertIsNone(self.plate.parent)
    self.assertIsNone(self.source.resource)

  async def test_a_coordinate_puts_its_centre_bottom_there(self):
    center = self.plate.center()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    await self.grippers.place_resource(Coordinate(600 + center.x, 150 + center.y, 100))
    self.assertEqual(self.sent, ["C0ZRxs06639xd0yj1927zj1101zi000zy0500yo0885th2800te2800"])
    self.assertIs(self.plate.parent, self.star.deck)
    self.assertEqual(self.plate.location, Coordinate(600, 150, 100))

  async def test_drop_resource_is_a_deprecated_place_resource(self):
    center = self.plate.center()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=6, grip_strength=40)
    with self.assertWarnsRegex(DeprecationWarning, "removed in the release after next"):
      await self.grippers.drop_resource(Coordinate(600 + center.x, 150 + center.y, 100))
    self.assertEqual(self.sent, ["C0ZRxs06639xd0yj1927zj1101zi000zy0500yo0885th2800te2800"])
    self.assertIs(self.plate.parent, self.star.deck)


class TestMounting(unittest.IsolatedAsyncioTestCase):
  """The tools out of the deck's holder and onto two channels, and back, as the model sees it."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.holder = self.grippers._holder()
    self.parked = {tool.name: tool.location for tool in self.holder.children}
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      if command in ("ZT", "ZS"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  def on(self, channel: int) -> str:
    shaft = self.grippers._pipettes.shaft(channel)
    assert shaft is not None and shaft.tip is not None
    return shaft.tip.name

  async def test_pick_up_takes_them_from_the_holder_as_legacy(self):
    await self.grippers.pick_up_tools()
    self.assertEqual(self.sent, ["C0ZTxs13375xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"])
    self.assertTrue(self.on(6).endswith("_back") and self.on(7).endswith("_front"))
    self.assertTrue(self.grippers.tools_mounted)
    self.assertEqual(self.holder.children, [])
    self.grippers._require_mounted()

  async def test_a_named_pair(self):
    await self.grippers.pick_up_tools(front_channel=5)
    self.assertIn("pa05pb06", self.sent[0])
    self.assertTrue(self.on(4).endswith("_back") and self.on(5).endswith("_front"))

  async def test_return_puts_them_back_where_they_were(self):
    await self.grippers.pick_up_tools()
    self.sent.clear()
    await self.grippers.return_tools()
    self.assertEqual(self.sent, ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"])
    self.assertEqual({tool.name: tool.location for tool in self.holder.children}, self.parked)
    self.assertFalse(self.grippers.tools_mounted)
    await self.grippers.return_tools()
    self.assertEqual(len(self.sent), 1)

  async def test_mounted_returns_them_when_the_block_raises(self):
    with self.assertRaises(RuntimeError):
      async with self.grippers.mounted():
        raise RuntimeError("the block")
    self.assertFalse(self.grippers.tools_mounted)
    self.assertEqual({tool.name: tool.location for tool in self.holder.children}, self.parked)

  async def test_an_empty_holder_is_refused(self):
    for tool in list(self.holder.children):
      self.holder.unassign_child_resource(tool)
    with self.assertRaises(TypeError):
      await self.grippers.pick_up_tools()
    self.assertEqual(self.sent, [])

  async def test_a_channel_sensing_nothing_puts_them_back_in_the_model(self):
    async def nothing() -> List[int]:
      return [0] * 8

    self.grippers._pipettes.sense_tip_presence = nothing  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools()
    self.assertFalse(self.grippers.tools_mounted)
    self.assertEqual({tool.name: tool.location for tool in self.holder.children}, self.parked)


class TestReturningToolsTheModelLost(unittest.IsolatedAsyncioTestCase):
  """After a restart the model holds no tools; the channels' overhangs say which carry them."""

  RETURN = "C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.holder = self.grippers._holder()
    self.parked = {tool.name: tool.location for tool in self.holder.children}
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def capturing(module: str, command: str, **kwargs: Any):
      if command == "ZS":
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      elif command in ("DI", "ZA"):
        self.sent.append(f"{module}{command}")
      answered = await answer(module=module, command=command, **kwargs)
      if command == "ZS":
        self.reading(overhangs={})  # the tools are back: the channels sense nothing
      return answered

    self.grippers._driver.send_command = capturing  # type: ignore[assignment]

  def reading(self, overhangs: Dict[int, float]) -> None:
    """What each channel reads below its stop disc, in mm; the others sense nothing."""
    pipettes = self.grippers._pipettes

    async def sense_tip_presence() -> List[int]:
      return [int(channel in overhangs) for channel in range(pipettes.num_channels)]

    async def request_tip_overhang(channel: int) -> float:
      return overhangs[channel]

    pipettes.sense_tip_presence = sense_tip_presence  # type: ignore[method-assign]
    pipettes.request_tip_overhang = request_tip_overhang  # type: ignore[method-assign]

  async def test_a_neighbouring_pair_at_the_tool_overhang_goes_back_to_the_holder(self):
    self.reading(overhangs={6: 22.0, 7: 22.0})
    with self.assertLogs(
      "pylabrobot.hamilton.star.driver.features.core_grippers", "WARNING"
    ) as log:
      await self.grippers.return_tools()
    self.assertIn("channels 6 and 7 hold CoRe gripper tools", log.output[0])
    self.assertEqual(self.sent, [self.RETURN, "C0ZA"])
    self.assertIsNone(self.grippers._tools_taken_from)
    self.assertFalse(self.grippers.tools_mounted)
    self.assertEqual({tool.name: tool.location for tool in self.holder.children}, self.parked)

  async def test_anything_but_one_neighbouring_pair_at_22_is_left_on(self):
    logger = "pylabrobot.hamilton.star.driver.features.core_grippers"
    for overhangs in (
      {6: 21.9, 7: 22.0},
      {6: 22.0, 7: 22.1},
      {7: 22.0},
      {4: 22.0, 6: 22.0},
      {5: 22.0, 6: 22.0, 7: 22.0},
    ):
      with self.subTest(overhangs=overhangs):
        self.reading(overhangs=overhangs)
        with self.assertLogs(logger, "WARNING") as log:
          await self.grippers.return_tools()
        self.assertIn("nothing is returned", log.output[0])
        self.assertEqual(self.sent, [])
    with self.subTest(overhangs="tips only"):
      self.reading(overhangs={0: 51.9, 2: 22.2})
      with self.assertNoLogs(logger, "WARNING"):
        await self.grippers.return_tools()
      self.assertEqual(self.sent, [])

  async def test_tools_taken_from_elsewhere_are_not_sent_to_the_holder(self):
    self.grippers._tools_taken_from = (500.0, 200.0, 180.0, 150.0, 140.0)
    self.reading(overhangs={6: 22.0, 7: 22.0})
    await self.grippers.return_tools()
    self.assertEqual(self.sent, [])

  async def test_setup_returns_them_and_initializes_nothing_when_only_they_were_on(self):
    self.reading(overhangs={6: 22.0, 7: 22.0})
    await self.grippers._driver._initialize_arm(arm=self.grippers.arm, already_initialized=True)
    self.assertEqual(self.sent[:2], [self.RETURN, "C0ZA"])
    self.assertNotIn("C0DI", self.sent)

  async def test_setup_raises_to_safe_z_and_initializes_when_they_are_not_the_tools(self):
    self.reading(overhangs={6: 21.9, 7: 22.0})
    await self.grippers._driver._initialize_arm(arm=self.grippers.arm, already_initialized=True)
    self.assertEqual(self.sent[:2], ["C0ZA", "C0DI"])


class TestSimulatedChannelPositions(unittest.IsolatedAsyncioTestCase):
  """The simulator records where each CoRe gripper command leaves the arm and the two channels."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    self.carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.star.deck.assign_child_resource(self.carrier, track=30)
    self.carrier[0] = self.plate = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    assert self.star.core_grippers is not None and self.star.pipettes is not None
    self.grippers = self.star.core_grippers
    self.pipettes = self.star.pipettes

  def pair(self) -> List[Coordinate]:
    back, front = self.grippers._back_channel, self.grippers._front_channel
    assert back is not None and front is not None
    locations = [self.pipettes.get_reference_point_location(channel) for channel in (back, front)]
    assert all(location is not None for location in locations)
    return [location for location in locations if location is not None]

  async def test_the_tools_are_taken_where_the_holder_holds_them(self):
    holder = self.grippers._holder()
    await self.grippers.pick_up_tools()
    back, front = self.pair()
    at = holder.get_location_wrt(self.star.deck, x="c")
    self.assertAlmostEqual(back.x, at.x, places=1)
    self.assertAlmostEqual(back.y, at.y + holder.back_channel_y_center, places=1)
    self.assertAlmostEqual(front.y, at.y + holder.front_channel_y_center, places=1)

  async def test_a_gripped_plate_hangs_between_the_channels_over_where_it_stood(self):
    centre = self.plate.get_location_wrt(self.star.deck, "c", "c", "b")
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=5)
    back, front = self.pair()
    self.assertAlmostEqual(back.x, centre.x, places=1)
    self.assertAlmostEqual((back.y + front.y) / 2, centre.y, places=1)
    # Closed to its width less 3 mm, the default squeeze either side.
    self.assertAlmostEqual(back.y - front.y, self.plate.get_absolute_size_y() - 3.0, places=0)
    held = self.plate.get_location_wrt(self.star.deck, "c", "c", "b")
    self.assertAlmostEqual(held.x, centre.x, places=1)
    self.assertAlmostEqual(held.y, centre.y, places=1)
