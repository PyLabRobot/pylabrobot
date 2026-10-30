"""A simulated PreciseFlex arm: set up, moved and gripped with nothing sent."""

from typing import Any, cast

import anyio
import pytest

from pylabrobot.brooks.precise_flex import PreciseFlex400, PreciseFlexAxis, PreciseFlexError
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.testing.concurrency import AnyioTestBase


class TestASimulatedArm(AnyioTestBase):
  """Driven like the real arm, answering from its declared configuration and its held joints."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    # `send_command` raises on a simulated arm: a lifespan that enters sent nothing.
    self.device = PreciseFlex400(simulation=True, name="pf400")
    await stack.enter_async_context(self.device.lifespan())
    self.driver = self.device.driver
    self.hand = cast(MechanicalGripper, self.driver.gripper.resource)

  async def test_it_stands_parked_with_its_jaws_open(self) -> None:
    joints = await self.driver.arm.request_joint_state()
    parked = self.driver.arm.PARKING_POSITION_RIGHT
    assert {axis: joints[axis] for axis in parked} == parked
    assert joints[PreciseFlexAxis.BASE] == 0.75 * 401.5
    assert self.hand.jaw_width == 134.0

  async def test_a_joint_move_moves_the_model(self) -> None:
    await self.driver.arm.move_to_joint_state({PreciseFlexAxis.WRIST: 170.0})
    assert (await self.driver.arm.request_joint_state())[PreciseFlexAxis.WRIST] == 170.0
    assert self.hand.rotation.z == pytest.approx(170.0)

  async def test_a_jaw_move_moves_the_jaws(self) -> None:
    await self.driver.gripper.move_to_jaw_position(100.0)
    assert self.hand.jaw_width == pytest.approx(100.0, abs=0.5)

  async def test_a_command_it_does_not_simulate_names_itself(self) -> None:
    with pytest.raises(NotImplementedError, match="'version'"):
      await self.driver.request_version()


class TestSimulatedLifespan(AnyioTestBase):
  async def test_lifespan_activates_deactivates_and_reuses_cleanly(self) -> None:
    device = PreciseFlex400(simulation=True, name="pf400")
    assert not device.is_active
    assert not device.driver.is_active
    with pytest.raises(RuntimeError, match="simulation link is not open"):
      await device.arm.request_joint_state()

    async with device:
      assert device.is_active
      assert device.driver.is_active
      assert await device.driver._is_robot_homed()
      await device.gripper.move_to_jaw_position(100.0)
      assert cast(MechanicalGripper, device.gripper.resource).jaw_width == pytest.approx(
        100.0, abs=0.5
      )

    assert not device.is_active
    assert not device.driver.is_active
    with pytest.raises(RuntimeError, match="simulation link is not open"):
      await device.arm.move_to_joint_state({PreciseFlexAxis.WRIST: 170.0})

    async with device.lifespan(skip_home=True):
      assert not await device.driver._is_robot_homed()

    async with device:
      assert device.is_active
      assert device.driver.is_active
      assert await device.driver._is_robot_homed()
      assert cast(MechanicalGripper, device.gripper.resource).jaw_width == 134.0

    assert not device.is_active
    assert not device.driver.is_active

  async def test_lifespan_cleans_up_on_body_error_and_cancellation(self) -> None:
    device = PreciseFlex400(simulation=True, name="pf400")

    with pytest.raises(RuntimeError, match="body boom"):
      async with device:
        await device.arm.start_freedrive_mode([1])
        raise RuntimeError("body boom")

    assert not device.is_active
    assert not device.driver.is_active
    assert not device.driver._freedrive_active

    entered = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def run_and_block() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with device:
          entered.set()
          await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_and_block)
      await entered.wait()
      scopes[0].cancel()

    assert not device.is_active
    assert not device.driver.is_active

  async def test_skip_home_leaves_simulated_arm_unhomed_and_validates_bool(self) -> None:
    device = PreciseFlex400(simulation=True, name="pf400")
    async with device.lifespan(skip_home=True):
      assert not await device.driver._is_robot_homed()

    with pytest.raises(TypeError, match="skip_home must be a bool"):
      async with device.lifespan(skip_home="yes"):  # type: ignore[arg-type]
        pass

  async def test_freedrive_scope_rejects_simulated_motion_until_exit(self) -> None:
    device = PreciseFlex400(simulation=True, name="pf400")
    async with device:
      await device.arm.start_freedrive_mode([])
      assert not device.driver._freedrive_active
      with pytest.raises(PreciseFlexError):
        await device.arm.start_freedrive_mode([PreciseFlexAxis.RAIL])
      async with device.arm.freedrive():
        with pytest.raises(RuntimeError, match="freedrive scope is active"):
          await device.arm.move_to_joint_state({PreciseFlexAxis.WRIST: 170.0})
        with pytest.raises(RuntimeError, match="freedrive scope is active"):
          await device.gripper.move_to_jaw_position(100.0)
      await device.arm.move_to_joint_state({PreciseFlexAxis.WRIST: 170.0})
      assert (await device.arm.request_joint_state())[PreciseFlexAxis.WRIST] == 170.0
