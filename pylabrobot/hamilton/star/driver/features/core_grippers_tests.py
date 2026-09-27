import dataclasses
import unittest
from typing import Any, List

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import STAR
from pylabrobot.hamilton.star.driver.errors import HardwareError, STARFirmwareError
from pylabrobot.hamilton.star.driver.features.x_arm_tests import RECORDED_DEVICE, declaring
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.azenta.plates import azenta_96_wellplate_200uL_Vb_4titudeframestar
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00, STARDeck, hamilton_tip_300uL


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

    async def recorded(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2}
      self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

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
    await self.grippers._unchecked_fw_drop_resource(8204, 2102, 1954, 0, 500, 885, 2800, 2800)
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
    await self.grippers._unchecked_fw_drop_resource(
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

    async def recorded(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2 and not isinstance(v, list)}
      self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

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


def z_drive_stalled(trace: int = 62) -> STARFirmwareError:
  """What a channel answers when its Z drive stalls: trace 62."""
  error = HardwareError("Z-drive movement error", trace, "P8ZPer02/62", "P8")
  return STARFirmwareError({"Pipetting channel 8": error}, "C0ZPer99/00 P8ZPer02/62")


class TestCheckResourceExists(unittest.IsolatedAsyncioTestCase):
  """Legacy's presence check: the jaws pushed down onto a plate stall on it."""

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

    async def recorded(module: str, command: str, **kwargs: Any):
      if command == "ZP":
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

  async def check(self, **kwargs: Any) -> bool:
    return await self.grippers.probe_resource_exists_using_ztouch(
      self.location, self.plate, **kwargs
    )

  async def test_refused_without_the_tools(self):
    with self.assertRaises(RuntimeError):
      await self.check(gripper_y_margin=9, enable_recovery=False)

  async def test_sends_what_legacy_sends_and_nothing_found_is_false(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    heights = {"minimum_traverse_height_start": 275.0, "minimum_traverse_height_end": 275.0}
    self.assertFalse(await self.check(gripper_y_margin=9, enable_recovery=False, **heights))
    self.assertEqual(
      self.sent, ["C0ZPxs08204xd0yj1142yv0050zj1934zy0600yo0675yg0675yw20th2750te2750"]
    )

  async def test_the_heights_default_to_the_traverse_height(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    await self.check(gripper_y_margin=9, enable_recovery=False)
    self.assertTrue(self.sent[0].endswith("th2800te2800"), self.sent[0])

  async def test_a_stalled_z_drive_is_found(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)

    async def stalls(**kwargs: Any):
      raise z_drive_stalled()

    self.grippers._unchecked_fw_pick_up_resource = stalls  # type: ignore[method-assign, assignment]
    self.assertTrue(await self.check(gripper_y_margin=9, enable_recovery=False))

  async def test_any_other_error_raises(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)

    async def fails(**kwargs: Any):
      raise z_drive_stalled(trace=61)

    self.grippers._unchecked_fw_pick_up_resource = fails  # type: ignore[method-assign, assignment]
    with self.assertRaises(ValueError):
      await self.check(gripper_y_margin=9, enable_recovery=False)

  async def test_a_width_out_of_range_sends_nothing(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    with self.assertRaises(ValueError):
      await self.check(gripper_y_margin=40, enable_recovery=False)
    self.assertEqual(self.sent, [])


class TestResourceByResource(unittest.IsolatedAsyncioTestCase):
  """`pick_up_resource`, `drop_resource` and `return_resource`: the wire and the tree."""

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

    async def recorded(module: str, command: str, **kwargs: Any):
      if command in ("ZP", "ZR"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

  async def test_refused_without_the_tools(self):
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_resource(self.plate)
    self.assertEqual(self.sent, [])

  async def test_pick_up_sends_what_legacy_sends_and_hangs_it_on_the_front_tool(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    # Legacy: the centre, 5 mm below the top, open to its width + 3.0 mm and closed to it - 3.0 mm.
    self.assertEqual(
      self.sent, ["C0ZPxs08204xd0yj1142yv0050zj1964zy0500yo0885yg0825yw15th2800te2800"]
    )
    self.assertIs(self.plate.parent, self.grippers._front_tool())

  async def test_drop_into_a_site_sends_the_put_down_and_the_tree_follows(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    await self.grippers.drop_resource(
      self.carrier[2], press_on_distance=1.5, x_acceleration_level=2
    )
    self.assertEqual(self.sent[1], "C0ZRxs08204xd0xg2yj3062zj1964zi015zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.carrier[2])
    self.assertIsNone(self.grippers._held_resource)

  async def test_a_coordinate_puts_its_centre_bottom_there_on_the_deck(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    await self.grippers.drop_resource(Coordinate(400.0, 200.0, 100.0))
    # Let go 5 mm below its top: 100 + 16.1 - 5.
    self.assertEqual(self.sent[1], "C0ZRxs04000xd0yj2000zj1111zi000zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.star.deck)
    ccb = self.plate.get_location_wrt(self.star.deck, "c", "c", "b")
    self.assertEqual((ccb.x, ccb.y, ccb.z), (400.0, 200.0, 100.0))

  async def test_a_coordinate_inside_a_carrier_is_refused_before_anything_is_sent(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    site = self.carrier[2].get_location_wrt(self.star.deck, "c", "c", "b")
    with self.assertRaisesRegex(ValueError, "plate_carrier"):
      await self.grippers.drop_resource(site)
    self.assertEqual(len(self.sent), 1)
    self.assertIs(self.grippers._held_resource, self.plate)

  async def test_return_puts_it_back_where_it_was_taken_from(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    await self.grippers.return_resource()
    self.assertEqual(self.sent[1], "C0ZRxs08204xd0yj1142zj1964zi000zy0500yo0885th2800te2800")
    self.assertIs(self.plate.parent, self.carrier[0])

  async def test_drop_without_holding_raises(self):
    await self.grippers.pick_up_tools()
    with self.assertRaises(RuntimeError):
      await self.grippers.drop_resource(self.carrier[2])
    with self.assertRaises(RuntimeError):
      await self.grippers.return_resource()

  async def test_a_second_pick_up_while_holding_is_refused(self):
    await self.grippers.pick_up_tools()
    await self.grippers.pick_up_resource(self.plate)
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_resource(self.plate)
    self.assertEqual(len(self.sent), 1)

  async def test_gripped_more_than_20_mm_below_its_top_is_refused(self):
    await self.grippers.pick_up_tools()
    with self.assertRaisesRegex(ValueError, "pipetting head"):
      await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=20.5)
    self.assertEqual(self.sent, [])
    await self.grippers.pick_up_resource(self.plate, pickup_distance_from_top=12.0)
    self.assertEqual(len(self.sent), 1)

  async def test_the_grip_line_is_checked_against_its_own_reach(self):
    await self.grippers.pick_up_tools()
    pipettes = self.grippers._pipettes
    low, high = pipettes.configuration.z_range
    # The tool's grip line is 22 mm below the stop disc and 2 mm above the tool's bottom.
    pipettes.configuration.z_range = (low, 196.4 + 22.0 - 0.5)
    with self.assertRaisesRegex(ValueError, "grip line reaches"):
      await self.grippers.pick_up_resource(
        self.plate, minimum_traverse_height_start=210.0, minimum_traverse_height_end=210.0
      )
    pipettes.configuration.z_range = (196.4 - 2.0 + 0.5, high)
    with self.assertRaisesRegex(ValueError, "grip line reaches"):
      await self.grippers.pick_up_resource(self.plate)
    self.assertEqual(self.sent, [])
    pipettes.configuration.z_range = (low, high)
    await self.grippers.pick_up_resource(self.plate)
    self.assertEqual(len(self.sent), 1)

  async def test_out_of_range_arguments_send_nothing(self):
    await self.grippers.pick_up_tools()
    for kwargs in ({"grip_strength": 100}, {"squeeze_mm": 50.0}, {"z_speed": 0.0}):
      with self.assertRaises(ValueError, msg=str(kwargs)):
        await self.grippers.pick_up_resource(self.plate, **kwargs)
    await self.grippers.pick_up_resource(self.plate)
    for drop_kwargs in ({"press_on_distance": 100.0}, {"x_acceleration_level": 6}):
      with self.assertRaises(ValueError, msg=str(drop_kwargs)):
        await self.grippers.drop_resource(self.carrier[2], **drop_kwargs)
    self.assertEqual(len(self.sent), 1)


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

    async def recorded(module: str, command: str, **kwargs: Any):
      if command in ("ZT", "ZS"):
        wire = {k: v for k, v in kwargs.items() if len(k) == 2}
        self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

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
