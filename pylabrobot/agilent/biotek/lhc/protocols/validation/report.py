"""What validation answers with."""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType


@dataclass(frozen=True)
class Rejection:
  """Why a step cannot run.

  Attributes:
    code: The instrument's code for this rejection.
    reason: What is wrong, in the words the instrument would use. Empty for the rejections the
      instrument reports as a code alone.
  """

  code: int
  reason: str = ""

  def __str__(self) -> str:
    """The rejection as one line.

    Returns:
      The reason and its code, or just the code when there is no reason.
    """
    return f"{self.reason} [{self.code}]" if self.reason else f"[{self.code}]"


@dataclass(frozen=True)
class StepReport:
  """What validation found about one step.

  Attributes:
    number: Which step this is, counting from 1.
    step_type: What the step does.
    rejection: Why it cannot run, or None when it can.
  """

  number: int
  step_type: StepType
  rejection: Rejection | None = None

  @property
  def ok(self) -> bool:
    """Whether this step can run."""
    return self.rejection is None

  def __str__(self) -> str:
    """The step as one line.

    Returns:
      The step's number and type, and why it cannot run.
    """
    state = "ok" if self.ok else str(self.rejection)
    return f"step {self.number} ({self.step_type.name}): {state}"


@dataclass
class ValidationReport:
  """What validation found about a protocol.

  Truthy when every step can run, so it reads as an answer to the question that was asked. Printing
  it lists only what cannot run.

  Attributes:
    steps: One report per step, in protocol order.
    rejection: Why the protocol as a whole cannot run, for a problem that belongs to no single
      step -- a plate the instrument does not work with, or one it will not accept.
  """

  steps: list[StepReport] = field(default_factory=list)
  rejection: Rejection | None = None

  def __bool__(self) -> bool:
    """Whether the whole protocol can run."""
    return self.rejection is None and all(step.ok for step in self.steps)

  @property
  def failures(self) -> list[StepReport]:
    """The steps that cannot run, in protocol order."""
    return [step for step in self.steps if not step.ok]

  def __str__(self) -> str:
    """The report as text.

    Returns:
      One line saying whether the protocol can run, followed by a line per step that cannot.
    """
    if self.rejection is not None:
      return f"protocol cannot run: {self.rejection}"
    failures = self.failures
    if not failures:
      return f"protocol can run: {len(self.steps)} steps"
    lines = [f"protocol cannot run: {len(failures)} of {len(self.steps)} steps"]
    lines += [f"  {step}" for step in failures]
    return "\n".join(lines)
