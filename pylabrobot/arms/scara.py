from collections.abc import Iterable
from typing import Optional, Union

from pylabrobot.arms.backend import AccessPattern, SCARABackend
from pylabrobot.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.arms.standard import JointCoords
from pylabrobot.machines.machine import Machine


class SCARA(Machine):
  """A robotic arm."""

  def __init__(self, backend: SCARABackend):
    super().__init__(backend=backend)
    self.backend: SCARABackend = backend

  async def move_to(
    self,
    position: Union[PreciseFlexCartesianCoords, Iterable[float]],
    **backend_kwargs,
  ) -> None:
    """Move the arm to a specified position in 3D space or joint space."""
    if isinstance(position, Iterable) and not isinstance(position, list):
      position = list(position)
    return await self.backend.move_to(position, **backend_kwargs)

  async def get_joint_position(self, **backend_kwargs) -> JointCoords:
    """Get the current position of the arm in joint space."""
    return await self.backend.get_joint_position(**backend_kwargs)

  async def get_cartesian_position(self, **backend_kwargs) -> PreciseFlexCartesianCoords:
    """Get the current position of the arm in 3D space."""
    return await self.backend.get_cartesian_position(**backend_kwargs)

  async def open_gripper(self, gripper_width: float, **backend_kwargs) -> None:
    return await self.backend.open_gripper(gripper_width=gripper_width, **backend_kwargs)

  async def close_gripper(self, gripper_width: float, **backend_kwargs) -> None:
    return await self.backend.close_gripper(gripper_width=gripper_width, **backend_kwargs)

  async def is_gripper_closed(self, **backend_kwargs) -> bool:
    return await self.backend.is_gripper_closed(**backend_kwargs)

  async def halt(self, **backend_kwargs) -> None:
    """Stop any ongoing movement of the arm."""
    return await self.backend.halt(**backend_kwargs)

  async def home(self, **backend_kwargs) -> None:
    """Home the arm to its default position."""
    return await self.backend.home(**backend_kwargs)

  async def move_to_safe(self, **backend_kwargs) -> None:
    """Move the arm to a predefined safe position."""
    return await self.backend.move_to_safe(**backend_kwargs)

  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or JointCoords)
      access: Access pattern defining how to approach the target.  Defaults to VerticalAccess() if not specified.
    """
    if isinstance(position, Iterable) and not isinstance(position, list):
      position = list(position)
    return await self.backend.approach(position, access=access, **backend_kwargs)

  async def pick_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    plate_width: float,
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Pick a plate from the specified position.

    Args:
      position: Target position for pickup
      access: Access pattern defining how to approach and retract.  Defaults to VerticalAccess() if not specified.
      plate_width: ripper width in millimeters used when gripping the plate.
    """
    if isinstance(position, Iterable) and not isinstance(position, list):
      position = list(position)
    return await self.backend.pick_plate(
      plate_width=plate_width, position=position, access=access, **backend_kwargs
    )

  async def place_plate(
    self,
    position: Union[PreciseFlexCartesianCoords, JointCoords],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Place a plate at the specified position.

    Args:
      position: Target position for placement
      access: Access pattern defining how to approach and retract.  Defaults to VerticalAccess() if not specified.
    """
    if isinstance(position, Iterable) and not isinstance(position, list):
      position = list(position)
    return await self.backend.place_plate(position, access=access, **backend_kwargs)
