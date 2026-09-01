import dataclasses
import unittest
from types import SimpleNamespace
from typing import Any, List, Optional, cast

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.driver.features.x_arm import XArm
from pylabrobot.hamilton.star.driver.simulator import (
  BARE_X_ARM,
  DEFAULT_STAR_CONFIGURATION,
  STARSimulationDriver,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck

# What each read answers, keyed by the command that asks: the arm at 362.9 mm, in tenths of a
# millimetre and in motor counts, as the drive reports it.
REPLIES = {"RX": "rx +0003629 +0000036290", "RS": "rs +0003629 +0000036290"}


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
  """A machine with an arm on each rail, set up."""
  both = dataclasses.replace(
    DEFAULT_STAR_CONFIGURATION,
    right_arm=BARE_X_ARM,
  )
  driver = STARSimulationDriver(configuration=both, deck=STARDeck())
  await driver.setup()
  return driver


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
    await XArm.switch_drive_power_off(arm)
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
    await XArm.switch_drive_power_off(arm)
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
  """The resource on the deck says where the arm is, and only the machine can change that."""

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
    arms = [
      child.name for child in cast(HamiltonDeck, driver.deck).children if child.category == "x_arm"
    ]
    self.assertEqual(sorted(arms), ["left_x_arm", "right_x_arm"])

  async def test_a_rejected_move_records_where_the_arm_stopped(self):
    """The arm stops somewhere neither the old position nor the target describes, so the machine
    is asked where it ended up. Driven against a stub rather than the simulator, whose reads answer
    from the model and so cannot report a stop the model does not know about."""
    driver = await _both_arms()
    resource = cast(HamiltonDeck, driver.deck).get_resource("left_x_arm")

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
    self.assertEqual(seated.x + resource.get_anchor(x=arm.reference_anchor).x, 640.0)

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
    resource = cast(HamiltonDeck, driver.deck).get_resource("left_x_arm")
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
    self.assertEqual(seated.x + resource.get_anchor(x=arm.reference_anchor).x, 500.0)

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
    arm.resource = cast(HamiltonDeck, driver.deck).get_resource("left_x_arm")
    await arm.move_x(500.0)
    self.assertEqual(reads, 5)
    seated = cast(Coordinate, arm.resource.location)
    self.assertEqual(seated.x + arm.resource.get_anchor(x=arm.reference_anchor).x, 500.0)

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
    arm.resource = cast(HamiltonDeck, driver.deck).get_resource("left_x_arm")
    with self.assertLogs("pylabrobot.hamilton.star.driver.features.x_arm", level="WARNING"):
      await arm.move_x(500.0, settle_reads=3)
    self.assertEqual(reads, 3)
    seated = cast(Coordinate, arm.resource.location)
    self.assertEqual(seated.x + arm.resource.get_anchor(x=arm.reference_anchor).x, 498.0)
