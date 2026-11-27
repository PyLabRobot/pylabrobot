"""Protocol steps: what they hold, how a protocol file stores them, how they are encoded."""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step


def step_from_definition(text: str) -> Step:
  """Read a step of any type back from its definition text.

  Which class it needs comes from the definition itself.

  Args:
    text: The ``|``-separated definition, or the ``#``-joined parts of a composite one.

  Returns:
    The step, of whichever class implements its type.

  Raises:
    ValueError: If the definition names no known step type, or does not have that type's layout.
  """
  from pylabrobot.agilent.biotek.lhc.protocols.steps.steps import step_class_for_definition

  return step_class_for_definition(text).from_definition(text)


__all__ = ["Step", "step_from_definition"]
