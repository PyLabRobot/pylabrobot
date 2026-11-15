"""Progress and boundary controls for a single CERTUS operation."""

import asyncio
from dataclasses import dataclass, replace
from typing import Literal, Optional

RunState = Literal["pending", "running", "paused", "completed", "aborted", "failed"]


class RunAborted(RuntimeError):
  """An operator aborted the run; submitted but unconfirmed doses must not be repeated."""


@dataclass(frozen=True)
class RunProgress:
  """Confirmed operation counts and an optional zero-based uncertain group index."""

  state: RunState = "pending"
  total_groups: int = 0
  completed_groups: int = 0
  completed_doses: int = 0
  uncertain_group: Optional[int] = None
  error: Optional[str] = None


class RunControl:
  """Single-use controls; pause takes effect at a completed, raised group boundary.

  Use ``await certus.abort()`` to abort the active operation and close its valves.
  Pausing or resuming never cancels an in-flight transport request.
  """

  def __init__(self) -> None:
    """Create a pending run that has not submitted any commands."""
    self._progress = RunProgress()
    self._resume = asyncio.Event()
    self._resume.set()
    self._paused = asyncio.Event()
    self._aborted = False
    self._abort_event = asyncio.Event()

  @property
  def progress(self) -> RunProgress:
    """Return an immutable snapshot suitable for a progress display."""
    return self._progress

  def _update(self, **changes) -> None:
    """Publish a progress snapshot without calling user code inside hardware operations."""
    self._progress = replace(self._progress, **changes)

  def pause(self) -> None:
    """Request a pause before the next group; the current group finishes first."""
    if self.progress.state not in ("pending", "running", "paused"):
      raise ValueError("A finished run cannot be paused")
    self._resume.clear()

  def resume(self) -> None:
    """Continue a paused run without replaying confirmed groups."""
    if self._aborted or self.progress.state not in ("pending", "running", "paused"):
      raise ValueError("A finished or aborted run cannot be resumed")
    self._resume.set()

  async def wait_paused(self) -> None:
    """Wait until the executor reaches a pause boundary (use an external timeout)."""
    await self._paused.wait()

  def _request_abort(self) -> None:
    """Wake a paused executor and prevent further dose submissions."""
    self._aborted = True
    self._abort_event.set()
    self._resume.set()

  def _check_abort(self) -> None:
    """Reject further execution after an abort request."""
    if self._aborted:
      raise RunAborted("CERTUS run aborted; inspect progress before preparing another run")

  async def _sleep(self, duration: float) -> None:
    """Wait between maintenance cycles while allowing an abort to wake the scheduler."""
    self._check_abort()
    try:
      await asyncio.wait_for(self._abort_event.wait(), duration)
    except asyncio.TimeoutError:
      return
    self._check_abort()

  def _start(self, total: int) -> None:
    """Claim this controller once; failed and completed recipes cannot be resumed."""
    if self.progress.state != "pending":
      raise ValueError("RunControl is single-use")
    self._update(state="running", total_groups=total)

  async def _boundary(self) -> None:
    """Wait only at an operation boundary, with no suspended liquid command."""
    self._check_abort()
    if not self._resume.is_set():
      self._update(state="paused")
      self._paused.set()
      await self._resume.wait()
      self._paused.clear()
    self._check_abort()
    self._update(state="running")


@dataclass(frozen=True)
class ControllerFault:
  """A pending controller issue, including native code, task, and descriptive text."""

  entity: str
  identifier: int
  code: int
  task: str
  text: str
  flags: int
  native_time: float
