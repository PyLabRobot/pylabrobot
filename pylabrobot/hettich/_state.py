"""Hettich workflow ownership and lifecycle state, independent of serial I/O."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import AsyncIterator, Literal, Optional

from ._errors import HettichCentrifugeError

ConnectionState = Literal["disconnected", "connecting", "connected", "disconnecting", "unknown"]
Activity = Literal[
  "idle",
  "setting_up",
  "disconnecting",
  "recovering",
  "opening_hatch",
  "closing_hatch",
  "positioning",
  "ending_positioning",
  "selecting_program",
  "preparing_to_spin",
  "accelerating",
  "at_speed",
  "braking",
  "stopping_spin",
]


@dataclass(frozen=True)
class HettichMachineState:
  """PLR workflow state; physical rotor and hatch facts come from live status queries.

  ``recovery_required`` persists after a failed operation that may have changed
  hardware or programmed settings. Only a successful ``recover()`` clears it.
  """

  connection: ConnectionState = "disconnected"
  activity: Activity = "idle"
  recovery_required: bool = False


@dataclass
class _Transition:
  """Record whether the active operation may have changed the device."""

  actuated: bool = False


class _StateMachine:
  """Own workflow transitions without caching physical device status."""

  def __init__(self) -> None:
    """Create a disconnected machine with a lazily allocated operation lock."""
    self._state = HettichMachineState()
    self._operation_lock: Optional[asyncio.Lock] = None
    self._transition: Optional[_Transition] = None

  @property
  def state(self) -> HettichMachineState:
    """Return the current immutable snapshot."""
    return self._state

  def set_connection(self, connection: ConnectionState) -> None:
    """Record transport lifecycle without clearing a recovery requirement."""
    self._state = replace(self.state, connection=connection)

  def set_activity(self, activity: Activity) -> None:
    """Record the owning workflow's current phase."""
    self._state = replace(self.state, activity=activity)

  def require_recovery(self) -> None:
    """Block ordinary motion until physical recovery checks succeed."""
    self._state = replace(self.state, recovery_required=True)

  def confirm_recovery(self) -> None:
    """Record successful recovery checks performed by the device driver."""
    self._state = replace(self.state, recovery_required=False)

  def mark_actuated(self) -> None:
    """Record possible device changes before transmitting a SELECT telegram."""
    if self._transition is not None:
      self._transition.actuated = True

  @asynccontextmanager
  async def operation(
    self, activity: Activity, *, require_connected: bool = True, allow_recovery: bool = False
  ) -> AsyncIterator[None]:
    """Reserve a complete workflow and retain uncertainty after possible actuation."""
    if self._operation_lock is None:
      self._operation_lock = asyncio.Lock()
    if self._operation_lock.locked():
      raise HettichCentrifugeError(f"Cannot {activity} while {self.state.activity} is active")
    async with self._operation_lock:
      if require_connected and self.state.connection != "connected":
        raise HettichCentrifugeError("The centrifuge is not connected; call setup() first")
      if self.state.recovery_required and not allow_recovery:
        raise HettichCentrifugeError(
          "The centrifuge requires recovery; call recover() before motion"
        )
      transition = _Transition()
      self._transition = transition
      self._state = replace(self.state, activity=activity)
      try:
        yield
      except BaseException:
        if transition.actuated:
          self._state = replace(self.state, recovery_required=True)
        raise
      finally:
        self._transition = None
        self._state = replace(self.state, activity="idle")
