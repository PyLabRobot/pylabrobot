"""Where in the well a step works."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Positioning:
  """An X, Y and Z offset from the nominal position for the plate in use.

  The values are the instrument's own motor steps, not millimetres, and the field names say so.
  This is what a protocol file stores and what a step command carries, and the conversion to
  millimetres is not one number: it differs per axis, per instrument model and per head. Keeping the
  step model in the unit the file uses is what lets a protocol be read and written without an
  instrument to ask.

  Positive Z is further down into the well. What range each axis accepts depends on the step and on
  the plate, and is checked by validation rather than here.

  Attributes:
    z_steps: Depth offset, in motor steps.
    x_steps: Offset across the plate, in motor steps.
    y_steps: Offset along the plate, in motor steps.
  """

  z_steps: int = 0
  x_steps: int = 0
  y_steps: int = 0

  def to_definition(self) -> str:
    """The three fields a protocol file stores, which are ordered Z, X, Y.

    Returns:
      The fields, ``|``-separated.
    """
    return f"{self.z_steps}|{self.x_steps}|{self.y_steps}"

  @classmethod
  def from_definition(cls, z: str, x: str, y: str) -> Positioning:
    """Read the three fields back.

    Widths differ per step, so the range check belongs to the step that owns these fields.

    Args:
      z: The depth field.
      x: The across-plate field.
      y: The along-plate field.

    Returns:
      The offsets.
    """
    return cls(z_steps=int(z), x_steps=int(x), y_steps=int(y))
