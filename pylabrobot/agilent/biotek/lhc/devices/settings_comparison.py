"""Measuring what a protocol says the instrument is against what the instrument says it is.

A protocol file carries the options the instrument had fitted when the protocol was written. Nothing
in this package runs against that record -- steps are encoded, and protocols checked, against what
the instrument reports now -- so its one use is telling a caller that the two disagree, which is
worth knowing before running a protocol written for another machine.

Only the options a protocol file actually stores are compared. Two fields of
:class:`~.instrument_settings.InstrumentSettings` are not in the document at all -- how many bottles
the syringe box holds, and whether the dispensers take the wider offset range -- so comparing them
would report a difference against a default that was never declared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings


def _fitted(present: bool) -> str:
  """Describe an option that is either fitted or not.

  Args:
    present: Whether it is fitted.

  Returns:
    The description.
  """
  return "fitted" if present else "not fitted"


def _enabled(on: bool) -> str:
  """Describe an option that is either switched on or not.

  Args:
    on: Whether it is on.

  Returns:
    The description.
  """
  return "enabled" if on else "not enabled"


OPTIONS: tuple[tuple[str, Callable[[InstrumentSettings], str]], ...] = (
  ("instrument model", lambda settings: settings.family.name),
  ("wash manifold", lambda settings: settings.washer_manifold.name),
  ("syringe box", lambda settings: settings.syringe_box.name),
  ("syringe manifold", lambda settings: settings.syringe_manifold.name),
  ("buffer switching module", lambda settings: _fitted(settings.buffer_switching)),
  ("valve box", lambda settings: settings.valve_box.name),
  ("vacuum filtration", lambda settings: _fitted(settings.vacuum_filtration)),
  ("primary peristaltic pump", lambda settings: _fitted(settings.peri_pump)),
  ("secondary peristaltic pump", lambda settings: _fitted(settings.peri_pump_2)),
  ("ultrasonic module", lambda settings: _fitted(settings.ultrasonic)),
  ("cell washing module", lambda settings: _fitted(settings.cell_washing)),
  ("Y axis", lambda settings: _fitted(settings.y_axis_installed)),
  ("half microlitre volumes", lambda settings: _enabled(settings.half_ul_enabled)),
  ("strip wash manifold", lambda settings: settings.strip_washer_manifold.name),
  ("single-well dispensing", lambda settings: _enabled(settings.single_well_enabled)),
  ("peristaltic washing", lambda settings: _enabled(settings.peri_wash_enabled)),
)
"""Every option a protocol file declares, and how to read it out of a settings record.

In the order the document stores them, so a comparison reads in the same order as the file.
"""


@dataclass(frozen=True)
class Difference:
  """One option a protocol declares differently from what the instrument reports.

  Attributes:
    option: Which option this is.
    declared: What the protocol says it is.
    actual: What the instrument says it is.
  """

  option: str
  declared: str
  actual: str

  def __str__(self) -> str:
    """The difference as one line.

    Returns:
      The option, what was declared, and what is fitted.
    """
    return f"{self.option}: protocol says {self.declared}, instrument reports {self.actual}"


@dataclass
class SettingsComparison:
  """How a protocol's declared options stand against the instrument's own.

  Truthy when they agree, so it reads as an answer to the question. Printing it lists only what
  differs.

  A difference is not on its own a reason not to run: whether a protocol can run is what
  :func:`~..protocols.validation.protocol_pass.validate` answers, and it measures every step
  against the instrument as it actually is. This says only that the protocol was written for a
  differently equipped machine.

  Attributes:
    differences: The options that do not match, in the order a protocol file stores them.
  """

  differences: list[Difference] = field(default_factory=list)

  def __bool__(self) -> bool:
    """Whether the protocol was written for an instrument equipped like this one."""
    return not self.differences

  def __str__(self) -> str:
    """The comparison as text.

    Returns:
      One line saying whether the two agree, followed by a line per option that does not.
    """
    if not self.differences:
      return "the protocol was written for an instrument equipped like this one"
    lines = [
      f"the protocol was written for a differently equipped instrument, in {len(self.differences)} respects"
    ]
    lines += [f"  {difference}" for difference in self.differences]
    return "\n".join(lines)


def compare(declared: InstrumentSettings, actual: InstrumentSettings) -> SettingsComparison:
  """Compare the options a protocol declares with the ones an instrument reports.

  Args:
    declared: What the protocol was written for.
    actual: What the instrument has fitted.

  Returns:
    The comparison, truthy when the two agree.
  """
  return SettingsComparison(
    [
      Difference(option, read(declared), read(actual))
      for option, read in OPTIONS
      if read(declared) != read(actual)
    ]
  )
