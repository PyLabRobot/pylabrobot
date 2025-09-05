from pylabrobot.arms.backend import ArmBackend
from pylabrobot.arms.precise_flex.preciseflex_api import PreciseFlexBackendApi


class PreciseFlexBackend(ArmBackend):
  """UNTESTED - Backend for the PreciseFlex robotic arm"""
  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__()
    self.api = PreciseFlexBackendApi(host=host, port=port, timeout=timeout)

  async def setup(self):
    """Initialize the PreciseFlex backend."""
    await self.api.setup()
    await self.set_pc_mode()
    await self.power_on_robot()
    await self.attach()

  async def stop(self):
    """Stop the PreciseFlex backend."""
    await self.detach()
    await self.power_off_robot()
    await self.exit()
    await self.api.stop()

  async def get_position(self):
    """Get the current position of the robot."""
    return await self.api.where_c()

  async def attach(self):
    """Attach the robot."""
    await self.api.attach(1)

  async def detach(self):
    """Detach the robot."""
    await self.api.attach(0)

  async def home(self):
    """Homes robot."""
    await self.api.home()

  async def home_all(self):
    """Homes all robots."""
    await self.api.home_all()

  async def power_on_robot(self):
    """Power on the robot."""
    await self.api.set_power(True, self.api.timeout)

  async def power_off_robot(self):
    """Power off the robot."""
    await self.api.set_power(False)

  async def set_pc_mode(self):
    """Set the controller to PC mode."""
    await self.api.set_mode(0)

  async def set_verbose_mode(self):
    """Set the controller to verbose mode."""
    await self.api.set_mode(1)

  async def select_robot(self, robot_id: int) -> None:
    """Select the specified robot."""
    await self.api.select_robot(robot_id)

  async def version(self) -> str:
    """Get the robot's version."""
    return await self.api.get_version()

  async def open_gripper(self):
    """Open the gripper."""
    await self.api.open_gripper()

  async def close_gripper(self):
    """Close the gripper."""
    await self.api.close_gripper()

  async def exit(self):
    """Exit the PreciseFlex backend."""
    await self.api.exit()


if __name__ == "__main__":

  async def main():
    arm = PreciseFlexBackend("192.168.0.1")
    await arm.setup()
    position = await arm.get_position()
    print(position)
    await arm.open_gripper()
    vals = await arm.get_motion_profile_values(1)
    print(vals)

  asyncio.run(main())