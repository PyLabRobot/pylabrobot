from typing import Callable, Optional

from pylabrobot.serializer import SerializableMixin

XArmTrackerCallback = Callable[[], None]


class XArmTracker(SerializableMixin):
  """Tracks the X-arm carriage reference point in x (mm, deck coordinates).

  The tracked value mirrors the firmware's carriage position report, rounded to the
  0.1 mm firmware granularity. Positions of components attached to the arm (channels,
  96-head, iSWAP rotation drive) are derived from this reference point via their x
  offsets and are not stored here.

  The position starts unknown and becomes known when the first tracked move commits.
  A failed move invalidates the position: after e.g. a collision the physical
  position cannot be inferred from the command, so the tracker returns to unknown.
  """

  def __init__(self, thing: str):
    self.thing = thing
    self._is_disabled = False
    self._x: Optional[float] = None
    self._pending_x: Optional[float] = None

    self._callback: Optional[XArmTrackerCallback] = None

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  @property
  def is_known(self) -> bool:
    """Whether the tracker has a committed position. Pending operations don't count."""
    return self._x is not None

  def disable(self) -> None:
    """Disable the tracker."""
    self._is_disabled = True

  def enable(self) -> None:
    """Enable the tracker."""
    self._is_disabled = False

  def get_x(self) -> float:
    """Get the committed X-arm carriage position in mm.

    Raises:
      RuntimeError: If no tracked move has committed yet, or the last tracked
        move failed.
    """
    if self._x is None:
      raise RuntimeError(f"{self.thing} position is unknown.")
    return self._x

  def set_x(self, x: float, commit: bool = True) -> None:
    """Record a commanded move target in mm, rounded to 0.1 mm.

    Args:
      x: The target position in mm.
      commit: Whether to commit the operation immediately. If `False`, the operation
        will be committed later with `commit()` or rolled back with `rollback()`.
    """
    if self.is_disabled:
      raise RuntimeError("X-arm tracker is disabled. Call `enable()`.")
    self._pending_x = round(x, 1)

    if commit:
      self.commit()

  def invalidate(self) -> None:
    """Mark the position as unknown, e.g. after a failed move."""
    if self.is_disabled:
      raise RuntimeError("X-arm tracker is disabled. Call `enable()`.")
    self._x = None
    self._pending_x = None

    if self._callback is not None:
      self._callback()

  def commit(self) -> None:
    """Commit the pending operations."""
    assert not self.is_disabled, "X-arm tracker is disabled. Call `enable()`."
    self._x = self._pending_x

    if self._callback is not None:
      self._callback()

  def rollback(self) -> None:
    """Rollback the pending operations."""
    assert not self.is_disabled, "X-arm tracker is disabled. Call `enable()`."
    self._pending_x = self._x

  def serialize(self) -> dict:
    """Serialize the state of the tracker."""
    return {
      "x": self._x,
      "pending_x": self._pending_x,
    }

  def load_state(self, state: dict) -> None:
    """Load a saved tracker state."""
    self._x = state["x"]
    self._pending_x = state["pending_x"]

  def register_callback(self, callback: XArmTrackerCallback) -> None:
    self._callback = callback

  def __repr__(self) -> str:
    return (
      f"XArmTracker({self.thing}, is_disabled={self.is_disabled}, x={self._x},"
      f" pending_x={self._pending_x})"
    )
