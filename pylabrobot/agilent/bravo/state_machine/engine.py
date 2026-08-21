"""Step-by-step task engine with abort/retry/ignore error recovery.

A :class:`StateMachineTask` breaks one Bravo operation (initialize, move,
aspirate, pick-and-place, ...) into an ordered list of named async steps.
:class:`StateMachineEngine` runs those steps in order. When a step raises,
the engine pauses the task, reports the failure through an error callback,
and waits for the caller to choose whether to abort the task, retry the
failed step, or ignore it and continue -- so an operator can recover a
workflow from a transient fault instead of the whole run dying.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
  """The lifecycle state of a :class:`StateMachineTask`."""

  PENDING = auto()
  RUNNING = auto()
  COMPLETED = auto()
  FAILED = auto()
  ABORTED = auto()
  PAUSED = auto()


class ErrorAction(Enum):
  """The operator's choice for recovering from a failed step."""

  ABORT = auto()
  RETRY = auto()
  IGNORE = auto()


@dataclass
class TaskError:
  """Details of a step failure.

  Attributes:
    message: The failure message, typically ``str(original_exception)``.
    step_name: The name of the step that failed.
    original_exception: The exception the step raised, if any.
  """

  message: str
  step_name: str
  original_exception: Optional[Exception] = None


class StateMachineTask(ABC):
  """Base class for one ordered, resumable Bravo operation.

  A task defines an ordered sequence of named async steps through
  :meth:`get_steps`. :class:`StateMachineEngine` executes them in order; if
  a step raises, the engine pauses and waits for an :class:`ErrorAction`
  before proceeding.
  """

  def __init__(self, name: str) -> None:
    """Initialize the task.

    Args:
      name: A human-readable name for this task instance, used in status
        payloads and log messages.
    """
    self.name = name
    self.status = TaskStatus.PENDING
    self._current_step_index = 0
    self._current_step_name: Optional[str] = None
    self.error: Optional[TaskError] = None
    # Universal operator-prompt slot. Step handlers can set this to a
    # dict like {kind, title, message, choices: [retry, ignore, abort]}
    # before raising, to show a task-specific modal. If left None, the
    # engine synthesizes a generic step_failed prompt so every failure is
    # recoverable.
    self._operator_prompt: Optional[dict] = None

  def status_payload(self) -> dict:
    """Return the task's current status for display to an operator.

    The default implementation surfaces the operator prompt (task-specific
    or engine-synthesized) whenever the task is in a failed state.
    Subclasses that compose a richer payload should call
    ``super().status_payload()`` and merge its result in.

    Returns:
      A dict with an ``"operator_prompt"`` key when the task is failed and
      a prompt is set, otherwise an empty dict.
    """
    if self.status == TaskStatus.FAILED and self._operator_prompt:
      return {"operator_prompt": dict(self._operator_prompt)}
    return {}

  def on_error_action(self, action: ErrorAction) -> None:
    """Handle the operator's choice after a step failure.

    The base implementation clears the operator prompt so a subsequent,
    distinct failure can populate its own. Subclasses should call
    ``super().on_error_action(action)`` before their own logic.

    Args:
      action: The action the operator chose.
    """
    self._operator_prompt = None

  @abstractmethod
  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order.

    Returns:
      An ordered list of ``(step_name, async_callable)`` pairs.
    """
    ...


class StateMachineEngine:
  """Runs :class:`StateMachineTask` instances step by step.

  Executes one task's steps in order. When a step raises, the engine fires
  its error callback and blocks until :meth:`abort`, :meth:`retry`, or
  :meth:`ignore` is called to resolve the failure.
  """

  def __init__(self) -> None:
    """Initialize the engine with no task running and no handlers set."""
    self._lock = asyncio.Lock()
    self._current_task: Optional[StateMachineTask] = None
    self._error_action_event = asyncio.Event()
    self._pending_action: Optional[ErrorAction] = None
    self._awaiting_error_action = False
    self._on_error: Optional[Callable[[TaskError], None]] = None
    self._on_step_complete: Optional[Callable[[str, str], None]] = None
    self._on_task_complete: Optional[Callable[[StateMachineTask], None]] = None

  def set_error_handler(self, handler: Callable[[TaskError], None]) -> None:
    """Set the callback invoked with a :class:`TaskError` when a step fails.

    Args:
      handler: The callback to invoke.
    """
    self._on_error = handler

  def set_step_handler(self, handler: Callable[[str, str], None]) -> None:
    """Set the callback invoked with ``(task_name, step_name)`` after each step.

    Args:
      handler: The callback to invoke.
    """
    self._on_step_complete = handler

  def set_completion_handler(self, handler: Callable[[StateMachineTask], None]) -> None:
    """Set the callback invoked with the task once it completes.

    Args:
      handler: The callback to invoke.
    """
    self._on_task_complete = handler

  async def execute(self, task: StateMachineTask) -> None:
    """Run a task's steps in order until it completes, is aborted, or fails.

    If a step raises and no error handler is set, the exception propagates
    to the caller instead of pausing for an :class:`ErrorAction`.

    Args:
      task: The task to run.
    """
    async with self._lock:
      self._current_task = task
      task.status = TaskStatus.RUNNING
      steps = task.get_steps()

      while task._current_step_index < len(steps):
        step_name, step_fn = steps[task._current_step_index]
        task._current_step_name = step_name
        try:
          await step_fn()
          if self._on_step_complete:
            self._on_step_complete(task.name, step_name)
          task._current_step_index += 1
        except Exception as exc:
          task.error = TaskError(
            message=str(exc),
            step_name=step_name,
            original_exception=exc,
          )
          task.status = TaskStatus.FAILED
          logger.error(
            "Task '%s' step '%s' failed: %s",
            task.name,
            step_name,
            exc,
          )

          if self._on_error:
            self._on_error(task.error)
          else:
            self._current_task = None
            raise

          try:
            payload = task.status_payload() or {}
          except Exception:
            payload = {}
          if not payload.get("operator_prompt"):
            # No task-specific prompt was set. Synthesize a generic
            # Retry/Ignore/Abort prompt so every state machine failure is
            # recoverable from the UI.
            fallback_step = step_name or "<unknown step>"
            task._operator_prompt = {
              "kind": "step_failed",
              "title": f"{task.name} failed",
              "message": (
                f"Step '{fallback_step}' raised:\n{exc!s}\n\n"
                "Retry re-runs the same step.\n"
                "Ignore skips this step and continues.\n"
                "Abort stops the workflow."
              ),
              "choices": ["retry", "ignore", "abort"],
              "step": fallback_step,
            }

          action = await self._wait_for_error_action()
          try:
            task.on_error_action(action)
          except Exception as hook_exc:
            logger.error("Task '%s' error-action hook failed: %s", task.name, hook_exc)
            task.status = TaskStatus.ABORTED
            self._current_task = None
            raise

          if action == ErrorAction.ABORT:
            task.status = TaskStatus.ABORTED
            self._current_task = None
            return
          elif action == ErrorAction.RETRY:
            task.status = TaskStatus.RUNNING
            continue
          elif action == ErrorAction.IGNORE:
            task.status = TaskStatus.RUNNING
            task._current_step_index += 1
            continue

      task._current_step_name = None
      task.status = TaskStatus.COMPLETED
      if self._on_task_complete:
        self._on_task_complete(task)
      self._current_task = None

  async def _wait_for_error_action(self) -> ErrorAction:
    """Block until an :class:`ErrorAction` is resolved for the current failure.

    Returns:
      The resolved action, defaulting to :attr:`ErrorAction.ABORT` if the
      event was set without a pending action recorded.
    """
    self._error_action_event.clear()
    self._pending_action = None
    self._awaiting_error_action = True
    try:
      await self._error_action_event.wait()
      return self._pending_action or ErrorAction.ABORT
    finally:
      self._awaiting_error_action = False

  def resolve_error(self, action: ErrorAction) -> bool:
    """Resolve the currently paused step failure with the given action.

    Args:
      action: The action to resolve the failure with.

    Returns:
      True if a paused failure was waiting and this call resolved it,
      False if no failure is currently paused or one was already resolved.
    """
    if not self._awaiting_error_action:
      return False
    if self._pending_action is not None:
      return False
    self._pending_action = action
    self._error_action_event.set()
    return True

  def abort(self) -> bool:
    """Resolve the current step failure with :attr:`ErrorAction.ABORT`.

    Returns:
      True if this call resolved a paused failure, False otherwise.
    """
    return self.resolve_error(ErrorAction.ABORT)

  def retry(self) -> bool:
    """Resolve the current step failure with :attr:`ErrorAction.RETRY`.

    Returns:
      True if this call resolved a paused failure, False otherwise.
    """
    return self.resolve_error(ErrorAction.RETRY)

  def ignore(self) -> bool:
    """Resolve the current step failure with :attr:`ErrorAction.IGNORE`.

    Returns:
      True if this call resolved a paused failure, False otherwise.
    """
    return self.resolve_error(ErrorAction.IGNORE)

  @property
  def current_task(self) -> Optional[StateMachineTask]:
    """The task currently executing, or ``None`` if the engine is idle."""
    return self._current_task

  @property
  def is_busy(self) -> bool:
    """Whether the engine currently has a task running."""
    return self._current_task is not None

  @property
  def awaiting_error_action(self) -> bool:
    """Whether the engine is currently paused waiting for an ErrorAction."""
    return self._awaiting_error_action
