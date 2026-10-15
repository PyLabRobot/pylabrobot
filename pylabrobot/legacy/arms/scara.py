from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import anyio

from pylabrobot.legacy.arms.backend import AccessPattern, SCARABackend
from pylabrobot.legacy.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.legacy.machines.machine import Machine


class ExperimentalSCARA(Machine):
  """A SCARA robotic arm. In experimental stage."""

  def __init__(self, backend: SCARABackend):
    super().__init__(backend=backend)
    self.backend: SCARABackend = backend
    self._in_freedrive = False
    self._freedrive_scope_token: Optional[object] = None
    self._freedrive_transition_lock = anyio.Lock()

  async def _ensure_not_freedrive_locked(self) -> None:
    """Exit durable freedrive before motion while holding the transition lock."""
    if self._freedrive_scope_token is not None:
      raise RuntimeError("Cannot move the arm while a freedrive scope is active")
    if self._in_freedrive:
      await self._end_freedrive_transition()

  async def move_to(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    **backend_kwargs,
  ) -> None:
    """Move the arm to a specified position in 3D space or joint space."""
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.move_to(position, **backend_kwargs)

  async def get_joint_position(self, **backend_kwargs) -> Dict[int, float]:
    """Get the current position of the arm in joint space."""
    return await self.backend.get_joint_position(**backend_kwargs)

  async def get_cartesian_position(self, **backend_kwargs) -> PreciseFlexCartesianCoords:
    """Get the current position of the arm in 3D space."""
    return await self.backend.get_cartesian_position(**backend_kwargs)

  async def open_gripper(self, gripper_width: float, **backend_kwargs) -> None:
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.open_gripper(gripper_width=gripper_width, **backend_kwargs)

  async def close_gripper(self, gripper_width: float, **backend_kwargs) -> None:
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.close_gripper(gripper_width=gripper_width, **backend_kwargs)

  async def is_gripper_closed(self, **backend_kwargs) -> bool:
    return await self.backend.is_gripper_closed(**backend_kwargs)

  async def halt(self, **backend_kwargs) -> None:
    """Stop any ongoing movement of the arm."""
    return await self.backend.halt(**backend_kwargs)

  async def home(self, **backend_kwargs) -> None:
    """Home the arm to its default position."""
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.home(**backend_kwargs)

  async def move_to_safe(self, **backend_kwargs) -> None:
    """Move the arm to a predefined safe position."""
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.move_to_safe(**backend_kwargs)

  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Move the arm to an approach position (offset from target).

    Args:
      position: Target position (CartesianCoords or joint position dict)
      access: Access pattern defining how to approach the target.  Defaults to VerticalAccess() if not specified.
    """
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.approach(position, access=access, **backend_kwargs)

  async def pick_up_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    plate_width: float,
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Pick a plate from the specified position.

    Args:
      position: Target position for pickup
      access: Access pattern defining how to approach and retract.  Defaults to VerticalAccess() if not specified.
      plate_width: gripper width in millimeters used when gripping the plate.
    """
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.pick_up_resource(
        plate_width=plate_width, position=position, access=access, **backend_kwargs
      )

  async def drop_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **backend_kwargs,
  ) -> None:
    """Place a plate at the specified position.

    Args:
      position: Target position for placement
      access: Access pattern defining how to approach and retract.  Defaults to VerticalAccess() if not specified.
    """
    async with self._freedrive_transition_lock:
      await self._ensure_not_freedrive_locked()
      return await self.backend.drop_resource(position, access=access, **backend_kwargs)

  async def freedrive_mode(self, free_axes: List[int], **backend_kwargs) -> None:
    """Enter freedrive mode, allowing manual movement of the specified joints.

    Args:
      free_axes: List of joint indices to free.
    """
    async with self._freedrive_transition_lock:
      if self._freedrive_scope_token is not None:
        raise RuntimeError("Cannot enter durable freedrive while a freedrive scope is active")
      await anyio.lowlevel.checkpoint()
      self._in_freedrive = True
      await self.backend.freedrive_mode(free_axes=free_axes, **backend_kwargs)
      await anyio.lowlevel.checkpoint()

  async def end_freedrive_mode(self, **backend_kwargs) -> None:
    """Exit freedrive mode."""
    async with self._freedrive_transition_lock:
      if self._freedrive_scope_token is not None:
        raise RuntimeError("Cannot end freedrive while a freedrive scope is active")
      await self._end_freedrive_transition(**backend_kwargs)

  async def _end_freedrive_transition(self, **backend_kwargs: Any) -> None:
    """Exit freedrive while retaining conservative state if the transition fails."""
    await self.backend.end_freedrive_mode(**backend_kwargs)
    self._in_freedrive = False

  @asynccontextmanager
  async def freedrive(self, free_axes: List[int], **backend_kwargs: Any) -> AsyncIterator[None]:
    """Enable freedrive for the lifetime of this context."""
    token = object()
    async with self._freedrive_transition_lock:
      if not self.is_active:
        raise RuntimeError("Freedrive scopes require an active ExperimentalSCARA lifespan")
      if self._freedrive_scope_token is not None:
        raise RuntimeError("Freedrive is already scoped")
      if self._in_freedrive:
        raise RuntimeError("Freedrive is already active")

      await anyio.lowlevel.checkpoint()
      self._in_freedrive = True
      try:
        await self.backend.freedrive_mode(free_axes=free_axes, **backend_kwargs)
        await anyio.lowlevel.checkpoint()
      except BaseException:
        with anyio.CancelScope(shield=True):
          await self._end_freedrive_transition()
        raise
      self._freedrive_scope_token = token

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._freedrive_transition_lock:
          if self._freedrive_scope_token is token:
            try:
              await self._end_freedrive_transition()
            finally:
              self._freedrive_scope_token = None

  async def _pre_backend_stop(self) -> None:
    """Force tracked freedrive off before closing the backend."""
    async with self._freedrive_transition_lock:
      if self._in_freedrive or self._freedrive_scope_token is not None:
        try:
          await self._end_freedrive_transition()
        finally:
          self._freedrive_scope_token = None
