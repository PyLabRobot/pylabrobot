"""What every protocol step is.

A step is a dataclass of parameters plus three conversions: to and from the text a protocol file
stores it as, and to the payload of the command that runs it. The command a step is sent as, and
the frame around that payload, belong to the serialization layer; whether a step *may* run belongs
to validation. A step itself only knows how to express its own parameters.
"""

from __future__ import annotations

import abc
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType


class Step(abc.ABC):
  """One step of a protocol.

  Attributes:
    step_type: Which operation this class performs.
  """

  step_type: ClassVar[StepType]

  @abc.abstractmethod
  def to_definition(self) -> str:
    """Write the step as the text a protocol file stores.

    Returns:
      The ``|``-separated definition, opening with the format marker and the step type.
    """

  @classmethod
  @abc.abstractmethod
  def from_definition(cls, text: str) -> Step:
    """Read a step of this type back from its definition text.

    Args:
      text: The ``|``-separated definition.

    Returns:
      The step.

    Raises:
      ValueError: If the definition does not have this type's layout.
    """

  @classmethod
  def default_definition(cls) -> str:
    """The definition text of this type with every field at its default.

    Read when a definition stops short of the layout's full length: the fields it does not carry
    are taken from here, which keeps the defaults in one place -- the dataclass -- rather than
    repeating them as text.

    Returns:
      The definition of a default step, up to the first sub-step. Only the type's own fields are
      ever filled in this way; a composite's sub-steps carry their own.
    """
    return cls().to_definition().split("#")[0]

  @abc.abstractmethod
  def to_bytes(self, settings: InstrumentSettings) -> bytes:
    """Encode the step as the payload of the command that runs it.

    The settings must be what the instrument reports, not what a protocol file declares: what is
    fitted changes the layout, widening an offset on the dispensers and adding a field to a strip
    dispense.

    Args:
      settings: What the instrument has fitted.

    Returns:
      The payload, padded to the length this step type sends.
    """
