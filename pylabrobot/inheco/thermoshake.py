import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import anyio

from pylabrobot.resources import Coordinate, ResourceHolder

from .control_box import InhecoTECControlBox
from .temperature_controller import InhecoTemperatureController

logger = logging.getLogger(__name__)


class InhecoThermoShake(ResourceHolder, InhecoTemperatureController):
  """Inheco ThermoShake: a temperature-controlled, shaking plate holder addressed by index on a
  control box.

  https://www.inheco.com/thermoshake-ac.html
  """

  def __init__(
    self,
    index: int,
    control_box: InhecoTECControlBox,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    child_location: Coordinate,
    category: str = "heating_shaking",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    InhecoTemperatureController.__init__(self, index=index, interface=control_box)
    self._shaking_transition_lock = anyio.Lock()
    self._shaking_active = False
    self._shaking_state_unknown = False
    self._shaking_scope_token: Optional[object] = None

  async def _start_shaking_command(self):
    return await self.interface.send_command(f"{self.index}ASE1")

  async def stop_shaking(self):
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot stop shaking while a shaking scope is active")
      return await self._stop_shaking_transition()

  async def _stop_shaking_transition(self):
    """Stop shaking while the caller holds the transition lock."""
    logger.info("[Inheco ThermoShake idx=%d] stop shaking", self.index)
    self._shaking_state_unknown = True
    try:
      result = await self.interface.send_command(f"{self.index}ASE0")
    except BaseException:
      self._shaking_active = True
      raise
    self._shaking_active = False
    self._shaking_state_unknown = False
    return result

  async def set_shaker_speed(self, speed: float):
    if not (60 <= speed <= 2000):
      raise ValueError("Speed must be in the range 60 to 2000 RPM")
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot set shaker speed while a shaking scope is active")
      return await self._set_shaker_speed(speed)

  async def _set_shaker_speed(self, speed: float):
    """Set shaker speed while the caller holds the transition lock."""
    return await self.interface.send_command(f"{self.index}SSR{speed}")

  async def set_shaker_shape(self, shape: int):
    """Set the shaking shape.

    Args:
      shape: 0 = Circle anticlockwise, 1 = Circle clockwise, 2 = Up left down right,
        3 = Up right down left, 4 = Up-down, 5 = Left-right
    """
    if shape not in range(6):
      raise ValueError("Shape must be in the range 0 to 5")
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot set shaker shape while a shaking scope is active")
      return await self._set_shaker_shape(shape)

  async def _set_shaker_shape(self, shape: int):
    """Set shaker shape while the caller holds the transition lock."""
    return await self.interface.send_command(f"{self.index}SSS{shape}")

  async def start_shaking(self, speed: float, shape: int = 0):
    if not (60 <= speed <= 2000):
      raise ValueError("Speed must be in the range 60 to 2000 RPM")
    if shape not in range(6):
      raise ValueError("Shape must be in the range 0 to 5")
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot start shaking while a shaking scope is active")
      await self._start_shaking_transition(speed, shape)

  async def _start_shaking_transition(self, speed: float, shape: int = 0) -> None:
    """Configure and start shaking while the caller holds the transition lock."""
    logger.info(
      "[Inheco ThermoShake idx=%d] start shaking: speed=%.0f, shape=%d", self.index, speed, shape
    )
    await self._set_shaker_speed(speed)
    await self._set_shaker_shape(shape)
    self._shaking_state_unknown = True
    await self._start_shaking_command()
    self._shaking_active = True
    self._shaking_state_unknown = False

  async def shake(self, speed: float, duration: float):
    async with self.shaking(speed=speed):
      await anyio.sleep(duration)

  @asynccontextmanager
  async def shaking(self, speed: float, shape: int = 0) -> AsyncIterator[None]:
    """Temporarily shake using an already-active borrowed control box."""
    if not (60 <= speed <= 2000):
      raise ValueError("Speed must be in the range 60 to 2000 RPM")
    if shape not in range(6):
      raise ValueError("Shape must be in the range 0 to 5")
    token = object()
    async with self._shaking_transition_lock:
      if not self.interface.is_active:
        raise RuntimeError("Shaking scopes require an active InhecoTECControlBox lifespan")
      if self._shaking_scope_token is not None:
        raise RuntimeError("Shaking is already scoped")
      if self._shaking_active or self._shaking_state_unknown:
        raise RuntimeError("Shaking is already active")
      await anyio.lowlevel.checkpoint()
      try:
        await self._start_shaking_transition(speed, shape)
      except BaseException:
        if self._shaking_active or self._shaking_state_unknown:
          with anyio.CancelScope(shield=True):
            await self._stop_shaking_transition()
        raise
      self._shaking_scope_token = token

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._shaking_transition_lock:
          if self._shaking_scope_token is token:
            try:
              await self._stop_shaking_transition()
            finally:
              self._shaking_scope_token = None


def inheco_thermoshake_ac(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake AC

  7100160, 7100161

  https://www.inheco.com/thermoshake-ac.html
  """

  raise NotImplementedError("Inheco ThermoShake AC is missing child_location.")

  return InhecoThermoShake(
    name=name,
    control_box=control_box,
    index=index,
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=115.9,  # from spec
    child_location=Coordinate(x=0, y=0, z=109.9),  # TODO
    model=inheco_thermoshake_ac.__name__,
  )


def inheco_thermoshake(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake (7100146)

  https://www.inheco.com/thermoshake-classic.html
  """

  return InhecoThermoShake(
    name=name,
    control_box=control_box,
    index=index,
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=118,  # from spec
    child_location=Coordinate(x=9.62, y=9.22, z=109.9),  # measured
    model=inheco_thermoshake.__name__,
    # pedestal_size_z=-4.2,  # measured
  )


def inheco_thermoshake_rm(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake RM (7100144)

  https://www.inheco.com/thermoshake-classic.html
  """

  raise NotImplementedError("Inheco Thermoshake RM is missing child_location")

  return InhecoThermoShake(
    name=name,
    control_box=control_box,
    index=index,
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=116,  # from spec
    child_location=Coordinate(x=0, y=0, z=0),  # TODO
    model=inheco_thermoshake_rm.__name__,
  )
