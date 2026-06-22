import unittest
from typing import Tuple
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.brooks.precise_flex import Axis, PreciseFlex400Backend, kinematics


def _make_backend(
  closed_gripper_position: float = 500.0,
) -> Tuple[PreciseFlex400Backend, MagicMock]:
  driver = MagicMock()
  driver.send_command = AsyncMock(return_value="")
  driver.io._host = "localhost"
  backend = PreciseFlex400Backend(
    driver=driver,
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=closed_gripper_position,
  )
  return backend, driver


class TestClassifyPF400Reach(unittest.TestCase):
  """Link lengths are classified as standard, extended, or unknown reach."""

  def test_classify_pf400_reach(self):
    self.assertEqual(kinematics._classify_pf400_reach((225, 210)), "standard")
    self.assertEqual(kinematics._classify_pf400_reach((302, 289)), "extended")
    self.assertEqual(kinematics._classify_pf400_reach((303, 288)), "extended")  # within tolerance
    self.assertEqual(kinematics._classify_pf400_reach((500, 500)), "unknown")


class TestPreciseFlex400Gripper(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    # closed_gripper_position=500 ⇒ min_gripper_width(60mm) maps to 500 units.
    self.backend, self.driver = _make_backend(closed_gripper_position=500.0)

  def _sent_commands(self) -> list[str]:
    return [c.args[0] for c in self.driver.send_command.call_args_list]

  async def test_move_gripper_force_sensing_false_opens_with_position(self):
    # 80 mm ⇒ 500 + (80 - 60) = 520 firmware units.
    await self.backend.move_gripper(width=80.0, force_sensing=False)
    self.assertEqual(self._sent_commands(), ["GripOpenPos 520.0", "gripper 1"])

  async def test_move_gripper_force_sensing_true_closes_with_position(self):
    # 60 mm (the closed reference) ⇒ exactly closed_gripper_position.
    await self.backend.move_gripper(width=60.0, force_sensing=True)
    self.assertEqual(self._sent_commands(), ["GripClosePos 500.0", "gripper 2"])

  async def test_move_gripper_position_command_precedes_move(self):
    await self.backend.move_gripper(width=120.0, force_sensing=False)
    commands = self._sent_commands()
    self.assertLess(
      commands.index("GripOpenPos 560.0"),
      commands.index("gripper 1"),
      "Position must be set before the gripper move command fires.",
    )

  async def test_force_sensing_branches_use_different_firmware_commands(self):
    await self.backend.move_gripper(width=90.0, force_sensing=False)
    await self.backend.move_gripper(width=90.0, force_sensing=True)
    commands = self._sent_commands()
    self.assertIn("gripper 1", commands)
    self.assertIn("gripper 2", commands)
    self.assertIn("GripOpenPos 530.0", commands)
    self.assertIn("GripClosePos 530.0", commands)

  async def test_min_max_gripper_width_advertised(self):
    self.assertEqual(self.backend.min_gripper_width, 60.0)
    self.assertEqual(self.backend.max_gripper_width, 145.0)

  async def test_closed_gripper_position_shifts_units(self):
    # Different anchor ⇒ same width yields a different firmware-unit target.
    backend, driver = _make_backend(closed_gripper_position=1000.0)
    await backend.move_gripper(width=80.0, force_sensing=False)
    commands = [c.args[0] for c in driver.send_command.call_args_list]
    # 80 mm ⇒ 1000 + (80 - 60) = 1020 units.
    self.assertEqual(commands, ["GripOpenPos 1020.0", "gripper 1"])

  def test_mm_to_firmware_units_helper(self):
    # Direct check of the linear mapping.
    self.assertEqual(self.backend._mm_to_firmware_units(60.0), 500.0)
    self.assertEqual(self.backend._mm_to_firmware_units(145.0), 585.0)
    self.assertEqual(self.backend._mm_to_firmware_units(100.0), 540.0)


class TestPreciseFlex400OutOfRangeRecovery(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend, self.driver = _make_backend()
    self.driver._wait_for_eom = AsyncMock()
    self.backend._request_speed = AsyncMock(return_value=50.0)
    # Minimal stub configuration: only the soft limits the recovery logic reads.
    self.backend._configuration = MagicMock(
      soft_limits={
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      }
    )

  def _move_one_axis_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in self.driver.send_command.call_args_list
      if c.args[0].startswith("MoveOneAxis")
    ]

  async def test_recover_moves_offenders_toward_limit_in_order_and_skips_wrist(self):
    """Each recoverable offender is driven 1 unit *inside* the violated limit (above-max down,
    below-min up), shoulder before elbow per _RECOVERY_ORDER; the wrist is never auto-moved."""
    self.backend.request_joint_position = AsyncMock(
      return_value={Axis.SHOULDER: 93.5, Axis.ELBOW: 9.0, Axis.WRIST: 962.0}
    )
    recovered = await self.backend.recover_axes_within_limits()
    self.assertEqual(recovered, {Axis.SHOULDER: 92.0, Axis.ELBOW: 13.0})  # wrist excluded
    cmds = self._move_one_axis_cmds()
    self.assertEqual(
      cmds, ["MoveOneAxis 2 92.0 1", "MoveOneAxis 3 13.0 1"]
    )  # shoulder (2) before elbow (3)

  async def test_recover_skips_axis_too_far_out_of_range(self):
    """An axis past its limit by more than max_distance is left in place (no unattended big sweep)."""
    self.backend.request_joint_position = AsyncMock(
      return_value={Axis.SHOULDER: 120.0}  # 27 deg past the 93 limit, beyond the 5 cap
    )
    recovered = await self.backend.recover_axes_within_limits()
    self.assertEqual(recovered, {})
    self.assertEqual(self._move_one_axis_cmds(), [])
