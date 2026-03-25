from typing import Dict, Optional, Union

from pylabrobot.arms.backend import AccessPattern
from pylabrobot.arms.six_axis_backend import SixAxisBackend
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.machines.machine import Machine


class SixAxisArm(Machine):
  """A 6-axis robotic arm frontend."""

  def __init__(self, backend: SixAxisBackend):
    super().__init__(backend=backend)
    self.backend: SixAxisBackend = backend
    self._in_freedrive = False

  async def _ensure_not_freedrive(self):
    if self._in_freedrive:
      await self.end_freedrive_mode()

  async def move_to(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    **backend_kwargs,
  ) -> None:
    """Move the arm to a specified position in Cartesian or joint space."""
    await self._ensure_not_freedrive()
    return await self.backend.move_to(position, **backend_kwargs)

  async def get_joint_position(self, **backend_kwargs) -> Dict[int, float]:
    """Get the current position of the arm in joint space."""
    return await self.backend.get_joint_position(**backend_kwargs)

  async def get_cartesian_position(self, **backend_kwargs) -> CartesianCoords:
    """Get the current position of the arm in Cartesian space."""
    return await self.backend.get_cartesian_position(**backend_kwargs)

  async def open_gripper(self, position: int, speed: int = 0, **backend_kwargs) -> None:
    """Open the arm's gripper.

    Args:
      position: Target open position (gripper-specific units).
      speed: Gripper speed (0 = default/max).
    """
    await self._ensure_not_freedrive()
    return await self.backend.open_gripper(position=position, speed=speed, **backend_kwargs)

  async def close_gripper(self, position: int, speed: int = 0, **backend_kwargs) -> None:
    """Close the arm's gripper.

    Args:
      position: Target close position (gripper-specific units).
      speed: Gripper speed (0 = default/max).
    """
    await self._ensure_not_freedrive()
    return await self.backend.close_gripper(position=position, speed=speed, **backend_kwargs)

  async def halt(self, **backend_kwargs) -> None:
    """Emergency stop any ongoing movement of the arm."""
    await self._ensure_not_freedrive()
    return await self.backend.halt(**backend_kwargs)

  async def home(self, **backend_kwargs) -> None:
    """Home the arm to its default position."""
    await self._ensure_not_freedrive()
    return await self.backend.home(**backend_kwargs)

  async def move_to_safe(self, **backend_kwargs) -> None:
    """Move the arm to a predefined safe position."""
    await self._ensure_not_freedrive()
    return await self.backend.move_to_safe(**backend_kwargs)

  async def approach(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or joint position dict)
      access: Access pattern defining how to approach the target.
              Defaults to VerticalAccess() if not specified.
    """
    await self._ensure_not_freedrive()
    return await self.backend.approach(position, access=access, **backend_kwargs)

  async def pick_up_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Pick a resource from the specified position.

    Args:
      position: Target position for pickup
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """
    await self._ensure_not_freedrive()
    return await self.backend.pick_up_resource(
      position=position, access=access, **backend_kwargs
    )

  async def drop_resource(
    self,
    position: Union[CartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Place a resource at the specified position.

    Args:
      position: Target position for placement
      access: Access pattern defining how to approach and retract.
              Defaults to VerticalAccess() if not specified.
    """
    await self._ensure_not_freedrive()
    return await self.backend.drop_resource(position, access=access, **backend_kwargs)

  async def freedrive_mode(self, **backend_kwargs) -> None:
    """Enter freedrive mode, allowing manual movement of all joints."""
    await self.backend.freedrive_mode(**backend_kwargs)
    self._in_freedrive = True

  async def end_freedrive_mode(self, **backend_kwargs) -> None:
    """Exit freedrive mode."""
    await self.backend.end_freedrive_mode(**backend_kwargs)
    self._in_freedrive = False
