import dataclasses
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, List, Optional, cast

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.features.x_arm import XArm, XArmConfiguration
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.serializer import serialize

# What each read answers, keyed by the command that asks: the arm at 362.9 mm, in tenths of a
# millimetre and in motor counts, as the drive reports it.
REPLIES = {"RX": "rx +0003629 +0000036290", "RS": "rs +0003629 +0000036290"}


# The device this package ships a recording of, read through the one reader there is: tests need a
# device to start from, and this is the one they stand in for.
RECORDED_DEVICE = cast(DeviceConfiguration, read_configuration(RECORDING_STAR)["device"])


def record(arm: XArm) -> List[str]:
  """Record what this arm sends, answering as its drive would. Returns the list it fills."""
  sent: List[str] = []

  async def recorded(module: str, command: str, fmt: Optional[Any] = None, **kwargs: Any):
    sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
    if command == "QW":
      return {"qw": 1}
    if command in REPLIES:
      return f"{module}{command}{REPLIES[command]}"
    return None

  arm._driver.send_command = recorded  # type: ignore[assignment]
  return sent


async def _both_arms() -> STARSimulationDriver:
  """A device with an arm on each rail, set up."""
  both = dataclasses.replace(
    RECORDED_DEVICE,
    right_arm=BARE_X_ARM,
  )
  driver = STARSimulationDriver(
    deck=STARDeck(),
    declared_configuration_json=declaring(device=both),
  )
  await driver.setup()
  return driver


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


class TestPerDriveCommands(unittest.IsolatedAsyncioTestCase):
  """The X-drive board carries a drive per arm, each with its own commands: the command's first
  letter and every parameter's first letter change with the drive. A relative move reads where the
  arm is and moves it absolutely, so it sends the same command an absolute move does - here the
  read is answered by the model, so only the move reaches the wire."""

  async def test_left_drive(self):
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    sent = record(arm)
    await XArm.initialize(arm)
    await XArm.move_x(arm, 500.0)
    await XArm.move_x_relative(arm, -12.5)
    await XArm._switch_drive_power_off(arm)
    self.assertEqual(
      sent,
      ["X0XIlw7", "X0XPla05000lr3lw7", "X0XPla04875lr3lw7", "X0XO"],
    )

  async def test_right_drive(self):
    driver = await _both_arms()
    arm = cast(XArm, driver.right_x_arm)
    sent = record(arm)
    await XArm.initialize(arm)
    await XArm.move_x(arm, 500.0)
    await XArm.move_x_relative(arm, -12.5)
    await XArm._switch_drive_power_off(arm)
    self.assertEqual(
      sent,
      ["X0SIsw7", "X0SPsa05000sr3sw7", "X0SPsa04875sr3sw7", "X0SO"],
    )

  async def test_reads_ask_about_this_arms_drive(self):
    driver = await _both_arms()
    for arm, status, position in (
      (cast(XArm, driver.left_x_arm), "X0QWmn1", "X0RX"),
      (cast(XArm, driver.right_x_arm), "X0QWmn2", "X0RS"),
    ):
      sent = record(arm)
      self.assertTrue(await XArm.request_initialization_status(arm))
      self.assertEqual(await XArm.request_position(arm), 362.9)
      self.assertEqual(sent, [status, position])


class TestModelFollowsTheArm(unittest.IsolatedAsyncioTestCase):
  """The resource on the deck says where the arm is, and only the device can change that."""

  async def test_a_move_moves_the_model(self):
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    await arm.move_x(500.0)
    self.assertEqual(await arm.request_position(), 500.0)

  async def test_a_refused_target_sends_nothing_and_moves_nothing(self):
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    await arm.move_x(500.0)
    sent = record(arm)
    with self.assertRaises(ValueError):
      await arm.move_x(5_000.0)
    self.assertEqual(sent, [])
    self.assertEqual(await arm.request_position(), 500.0)

  async def test_setup_does_not_duplicate_the_arm(self):
    driver = await _both_arms()
    await driver.setup()
    deck = cast(HamiltonDeck, driver.deck)
    arms = [child.name for child in deck.children if child.category == "x_arm"]
    self.assertEqual(sorted(arms), [deck.prefixed("left_x_arm"), deck.prefixed("right_x_arm")])

  async def test_a_rejected_move_records_where_the_arm_stopped(self):
    """The arm stops somewhere neither the old position nor the target describes, so the device
    is asked where it ended up. Driven against a stub rather than the simulator, whose reads answer
    from the model and so cannot report a stop the model does not know about."""
    driver = await _both_arms()
    deck = cast(HamiltonDeck, driver.deck)
    resource = deck.get_resource(deck.prefixed("left_x_arm"))

    async def refuse(module: str, command: str, fmt=None, **kwargs):
      if command == "XP":
        raise RuntimeError("error 51: drive blocked")
      return f"{module}{command}rx +0006400 +0000064000"  # 640 mm, part way to the target

    arm = XArm(
      SimpleNamespace(
        left_side_panel_installed=False, configuration=driver.configuration, send_command=refuse
      ),  # type: ignore[arg-type]
      side="left",
    )
    arm.resource = resource
    with self.assertRaises(RuntimeError):
      await arm.move_x(900.0)
    seated = cast(Coordinate, resource.location)
    self.assertEqual(seated.x + arm.configuration.reference_point_from_left, 640.0)

  async def test_a_failed_recovery_leaves_the_moves_own_error(self):
    driver = await _both_arms()

    async def refuse_everything(module: str, command: str, fmt=None, **kwargs):
      raise RuntimeError("error 51: drive blocked" if command == "XP" else "no answer")

    arm = XArm(
      SimpleNamespace(
        left_side_panel_installed=False,
        configuration=driver.configuration,
        send_command=refuse_everything,
      ),  # type: ignore[arg-type]
      side="left",
    )
    with self.assertRaises(RuntimeError) as raised:
      await arm.move_x(900.0)
    self.assertIn("drive blocked", str(raised.exception))

  async def test_a_relative_move_is_bounded_like_an_absolute_one(self):
    """It resolves to a target, so a distance that would take the arm off the rail is refused
    before anything reaches the wire."""
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    await arm.move_x(500.0)
    sent = record(arm)
    with self.assertRaises(ValueError):
      await arm.move_x_relative(5_000.0)
    self.assertEqual(sent, [])
    self.assertEqual(await arm.request_position(), 500.0)

  async def test_a_move_waits_until_the_arm_is_at_the_target(self):
    """The reply comes before the arm has stopped, so the move reads until two reads in a row find
    it at the target. Driven against a stub, since a simulated read answers from the model."""
    driver = await _both_arms()
    deck = cast(HamiltonDeck, driver.deck)
    resource = deck.get_resource(deck.prefixed("left_x_arm"))
    approach = iter(
      [
        "rx +0004985 +0000049850",  # still arriving
        "rx +0005003 +0000050030",  # past it
        "rx +0005000 +0000050000",  # there
        "rx +0005000 +0000050000",  # and still there
      ]
    )
    sent: List[str] = []

    async def answer(module: str, command: str, fmt=None, **kwargs):
      sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
      if command != "RX":
        return None
      return f"{module}{command}{next(approach, 'rx +0005000 +0000050000')}"

    arm = XArm(
      SimpleNamespace(
        left_side_panel_installed=False, configuration=driver.configuration, send_command=answer
      ),  # type: ignore[arg-type]
      side="left",
    )
    arm.resource = resource
    await arm.move_x(500.0)
    self.assertEqual(sent.count("X0RX"), 4)
    seated = cast(Coordinate, resource.location)
    self.assertEqual(seated.x + arm.configuration.reference_point_from_left, 500.0)

  async def test_an_arm_reversing_is_not_mistaken_for_one_at_the_target(self):
    """An arm at the end of a swing is momentarily still, so reads across that moment agree with
    each other - but not with the target, which is what the move waits for."""
    driver = await _both_arms()
    swing = iter(
      [
        "rx +0005004 +0000050040",  # the top of the overshoot, twice: still, but not there
        "rx +0005004 +0000050040",
        "rx +0005001 +0000050010",
        "rx +0005000 +0000050000",
        "rx +0005000 +0000050000",
      ]
    )
    reads = 0

    async def answer(module: str, command: str, fmt=None, **kwargs):
      nonlocal reads
      if command != "RX":
        return None
      reads += 1
      return f"{module}{command}{next(swing, 'rx +0005000 +0000050000')}"

    arm = XArm(
      SimpleNamespace(
        left_side_panel_installed=False, configuration=driver.configuration, send_command=answer
      ),  # type: ignore[arg-type]
      side="left",
    )
    deck = cast(HamiltonDeck, driver.deck)
    arm.resource = deck.get_resource(deck.prefixed("left_x_arm"))
    await arm.move_x(500.0)
    self.assertEqual(reads, 5)
    seated = cast(Coordinate, arm.resource.location)
    self.assertEqual(seated.x + arm.configuration.reference_point_from_left, 500.0)

  async def test_an_arm_that_never_arrives_gives_up_and_keeps_what_it_read(self):
    driver = await _both_arms()
    reads = 0

    async def answer(module: str, command: str, fmt=None, **kwargs):
      nonlocal reads
      if command != "RX":
        return None
      reads += 1
      return f"{module}{command}rx +0004980 +0000049800"  # 498.0, and it stays there

    arm = XArm(
      SimpleNamespace(
        left_side_panel_installed=False, configuration=driver.configuration, send_command=answer
      ),  # type: ignore[arg-type]
      side="left",
    )
    deck = cast(HamiltonDeck, driver.deck)
    arm.resource = deck.get_resource(deck.prefixed("left_x_arm"))
    with self.assertLogs("pylabrobot.hamilton.star.driver.features.x_arm", level="WARNING"):
      await arm.move_x(500.0, settle_reads=3)
    self.assertEqual(reads, 3)
    seated = cast(Coordinate, arm.resource.location)
    self.assertEqual(seated.x + arm.configuration.reference_point_from_left, 498.0)


class TestConfiguringAnArm(unittest.IsolatedAsyncioTestCase):
  """An arm's configuration is a field of the device's, since the device reports both arms in one
  reply. Writing to it goes through to that field, and what no device answers survives a re-read.
  """

  async def test_what_is_written_is_where_the_device_holds_it(self):
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    corrected = dataclasses.replace(arm.configuration, current_limit_range=(0, 15))

    arm.configuration = corrected

    self.assertEqual(arm.configuration.current_limit_range, (0, 15))
    self.assertIs(cast(DeviceConfiguration, driver.configuration).left_arm, corrected)

  async def test_the_constructor_takes_one_too(self):
    """The same write, done where every other feature takes its configuration."""
    driver = await _both_arms()
    corrected = dataclasses.replace(
      cast(XArm, driver.left_x_arm).configuration, current_limit_default=3
    )

    arm = XArm(driver, side="left", configuration=corrected)

    self.assertEqual(arm.configuration.current_limit_default, 3)
    self.assertIs(cast(DeviceConfiguration, driver.configuration).left_arm, corrected)

  async def test_it_refuses_before_the_device_has_been_read(self):
    """There is nowhere to put it: the field it writes to belongs to a configuration that is only
    built once the device has answered."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    with self.assertRaises(RuntimeError):
      XArm(driver, side="left", configuration=BARE_X_ARM)

  async def test_a_simulated_device_then_answers_what_was_written(self):
    """A simulated device answers from the configuration it was given, and its discovery keeps an
    arm's device facts across a re-read as a physical device's does - so what was written stays."""
    driver = await _both_arms()
    arm = cast(XArm, driver.left_x_arm)
    arm.configuration = dataclasses.replace(arm.configuration, current_limit_default=3)

    await driver.discover()

    self.assertEqual(arm.configuration.current_limit_default, 3)

  def test_device_facts_carry_over_and_readings_do_not(self):
    """What a physical device's discovery does with a configured arm. It rebuilds one from the
    reply, then takes the device facts off the arm as it was configured: those are what no device
    answers, so a re-read must not put them back to what this generation documents."""
    configured = dataclasses.replace(
      BARE_X_ARM, current_limit_range=(0, 15), current_limit_default=15
    )
    answered = dataclasses.replace(BARE_X_ARM, width=354.0, x_range=(95.0, 1340.2))

    kept = answered.with_device_facts_of(configured)

    self.assertEqual(kept.current_limit_range, (0, 15))
    self.assertEqual(kept.current_limit_default, 15)
    self.assertEqual(kept.width, 354.0)
    self.assertEqual(kept.x_range, (95.0, 1340.2))
    # Neither of the two it was worked out from is changed.
    self.assertEqual(configured.width, BARE_X_ARM.width)
    self.assertEqual(answered.current_limit_default, BARE_X_ARM.current_limit_default)


class TestGeometryDoesNotFollowTheReportedWidth(unittest.TestCase):
  """The arm is a part; the width a drive reports about it is a setting.

  `xu` is written into the instrument rather than measured off it, and a device left at the
  default answers 370.0 for the same arm another answers 354.0 for. So nothing about the arm's
  geometry may move when that number does.
  """

  # What devices have actually answered - 354.0 on one large arm, 370.0 the default another was
  # left at, 246.0 on a half arm - and then the largest the field can hold, which is four digits
  # in tenths of a millimetre. No arm is any of the last two sizes, and that is the point: the
  # part must not follow the number, whether the number is a default, another arm's, or absurd.
  REPORTED_WIDTHS = (354.0, 370.0, 246.0, 999.9)

  def test_a_large_arm_is_the_same_part_whatever_width_it_reports(self):
    for width in self.REPORTED_WIDTHS:
      with self.subTest(width=width):
        arm = XArmConfiguration(width=width, large=True)
        self.assertEqual(arm.size_x, 400.49)
        self.assertEqual(arm.reference_point_from_left, 223.00)

  def test_the_reported_width_is_the_reach_to_the_right_edge_doubled(self):
    """What the number does mean, checked against the part rather than assumed."""
    reported = 354.0
    arm = XArmConfiguration(width=reported, large=True)
    reach = arm.size_x - arm.reference_point_from_left
    self.assertAlmostEqual(2 * reach, reported, delta=1.0)

  def test_the_variant_comes_from_the_drive_size_bit_not_the_width(self):
    wide_but_small = XArmConfiguration(width=370.0, large=False)
    narrow_but_large = XArmConfiguration(width=246.0, large=True)
    self.assertEqual(wide_but_small.model, "hamilton_legacy_star_single_right_rail_arm")
    self.assertEqual(narrow_but_large.model, "hamilton_legacy_star_dual_rail_arm")
    self.assertEqual(wide_but_small.reference_point, "right")
    self.assertEqual(narrow_but_large.reference_point, "center")

  def test_a_configuration_without_the_bit_falls_back_to_the_width(self):
    """A declaration written before the bit was carried has only the width to go on."""
    self.assertIs(XArmConfiguration(width=354.0).is_large, True)
    self.assertIs(XArmConfiguration(width=246.0).is_large, False)
    self.assertIsNone(XArmConfiguration().is_large)

  def test_a_small_arm_has_not_been_measured(self):
    """It refuses rather than answering with the large arm's numbers."""
    small = XArmConfiguration(width=246.0, large=False)
    with self.assertRaises(RuntimeError):
      _ = small.size_x
    with self.assertRaises(RuntimeError):
      _ = small.reference_point_from_left
