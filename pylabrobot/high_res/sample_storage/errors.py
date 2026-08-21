from typing import List


class HighResSampleStorageError(Exception):
  """A command returned an ``ERROR!`` completion status.

  The TundraStore reports failures as an error stack: the completion line is
  preceded (firmware 3.0.x) by one or more ``Error <n>: ...`` lines, the last of
  which is generally the most pertinent. Those lines are preserved in
  :attr:`error_lines`.
  """

  def __init__(self, command: str, error_lines: List[str]):
    self.command = command
    self.error_lines = error_lines
    detail = error_lines[-1] if error_lines else "no error detail returned"
    super().__init__(f"'{command}' failed: {detail}")


class HighResSampleStorageAbortedError(Exception):
  """A command returned an ``ABORTED!`` completion status (e.g. after ``abort``)."""

  def __init__(self, command: str):
    self.command = command
    super().__init__(f"'{command}' was aborted")


class PlateNotFoundError(HighResSampleStorageError):
  """A pick found no plate in the target slot ("No plate detected").

  This is the normal *empty slot* outcome: the store's height detector reports
  the absence and the machine stays homed and operational — not a fault.
  Contrast with :class:`HighResSampleStorageFault` (the machine de-homed). Note that an
  empty *top* slot raises a fault instead, because the firmware can't complete
  its safe-travel retract from the topmost position.
  """


class HighResSampleStorageFault(HighResSampleStorageError):
  """A motion command faulted and left the machine UNHOMED/extended.

  The canonical trigger is picking an empty *top* slot. The machine is not
  usable until recovered — call
  :meth:`HighResSampleStorageAutomatedRetrievalBackend.recover` (retract the
  spatula and re-home) before issuing further motion.
  """

  def __init__(self, command: str, error_lines: List[str]):
    super().__init__(command, error_lines)
    self.args = (f"{self.args[0]}; machine is unsafe — call recover()",)


# Error-stack substrings meaning the spatula was left extended / unsafe to move,
# even when ``homedstatus`` still reports homed (it does exactly that when the
# spatula is stuck extended at a top slot). Recover before any further motion.
_UNSAFE_SIGNATURES = (
  "unsafe for rotation",
  "safe travel position",
  "crash occurred",
  "sensor was tripped",
  "machine must be homed",
)


def left_unsafe(error_lines: List[str]) -> bool:
  """Whether an error stack indicates the machine was left unsafe (spatula
  extended / unhomed), requiring
  :meth:`HighResSampleStorageAutomatedRetrievalBackend.recover`."""
  blob = " ".join(error_lines).lower()
  return any(sig in blob for sig in _UNSAFE_SIGNATURES)
