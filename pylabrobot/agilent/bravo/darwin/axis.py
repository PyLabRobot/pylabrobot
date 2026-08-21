"""Per-axis state machines -- commutate, home, initialize.

Commutation, homing, and initialization are driven directly through
``MOTOR_STATE`` writes and polled reads rather than through a single
firmware initialize call. Driving them step by step is what makes the
retry-on-regression semantics and the timing-sensitive behavior possible.

Public entry points:
  :func:`read_motor_state`, :func:`set_motor_state`
  :func:`commutate`, :func:`home`, :func:`initialize`
  :func:`enable`, :func:`disable`, :func:`is_enabled`, :func:`reset_faults`
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable, Dict, Optional

from ..errors import BravoError, ErrorType
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import GeminiSubCommands, MotorState
from ..protocol.gemini.packet import InstructionAddress
from ..types import Axis

# Default polling, timeout, and retry values.
_STATE_POLL = 0.2
_DEFAULT_COMMUTATE_TIMEOUT = 15.0
_DEFAULT_HOME_TIMEOUT = 20.0
_COMMUTATE_RETRIES = 2
_HOMING_RETRIES = 3


@dataclass(frozen=True)
class AxisTimeouts:
  """Per-axis timing overrides.

  Attributes:
    commutate: Commutation timeout override, in seconds. ``None`` means use
      the default.
    home: Homing timeout override, in seconds. ``None`` means use the
      default.
  """

  commutate: Optional[float] = None
  home: Optional[float] = None


# G axis has an extended commutate timeout (30s).
# W axis has an extended home timeout (40s).
_AXIS_TIMEOUTS: Dict[Axis, AxisTimeouts] = {
  "g": AxisTimeouts(commutate=30.0),
  "w": AxisTimeouts(home=40.0),
}


def timeouts_for(axis: Axis) -> AxisTimeouts:
  """Return the timing overrides for an axis.

  Args:
    axis: The axis to look up.

  Returns:
    The axis's timing overrides, or a default (all-``None``) record if it
    has none.
  """
  return _AXIS_TIMEOUTS.get(axis, AxisTimeouts())


# --- Primitive state read/write ---------------------------------------------


def read_motor_state(
  engine: GeminiEngine, address: InstructionAddress, timeout: float = 5.0
) -> MotorState:
  """Read an axis device's current motor-lifecycle state.

  Args:
    engine: The Gemini engine to read through.
    address: The axis device's controller-tree address.
    timeout: Maximum time to wait for the wire exchange, in seconds.

  Returns:
    The decoded state, or :attr:`~.enums.MotorState.INITIAL` if the device
    reported a value with no matching :class:`~.enums.MotorState` member.
  """
  raw = engine.get_value(address, GeminiSubCommands.MOTOR_STATE, timeout)
  try:
    return MotorState(raw)
  except ValueError:
    return MotorState.INITIAL


def set_motor_state(
  engine: GeminiEngine,
  address: InstructionAddress,
  state: MotorState,
  timeout: float = 5.0,
) -> None:
  """Write an axis device's motor-lifecycle state.

  Args:
    engine: The Gemini engine to write through.
    address: The axis device's controller-tree address.
    state: The state to request.
    timeout: Maximum time to wait for the wire exchange, in seconds.
  """
  engine.set_uint(address, GeminiSubCommands.MOTOR_STATE, int(state), timeout)


# --- Commutate ----------------------------------------------------------------


def commutate(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  *,
  timeout: Optional[float] = None,
  poll: float = _STATE_POLL,
  get_estop_engaged: Callable[[], bool] = lambda: False,
) -> None:
  """Commutate the axis: set state to Commutate, wait for Commutated.

  Retries up to :data:`_COMMUTATE_RETRIES` times if the state regresses to
  ``INITIAL``.

  Args:
    engine: The Gemini engine to drive the axis through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    timeout: Overall commutation timeout, in seconds; defaults to
      :data:`_DEFAULT_COMMUTATE_TIMEOUT`.
    poll: Delay between state polls, in seconds.
    get_estop_engaged: Returns True if E-stop is engaged; checked once at
      entry.

  Raises:
    BravoError: If E-stop is engaged at entry, the timeout elapses before
      the axis reaches ``COMMUTATED``, or the axis regresses to
      ``INITIAL`` more than :data:`_COMMUTATE_RETRIES` times.
  """
  if get_estop_engaged():
    raise BravoError(ErrorType.ROBOT_DISABLE)

  deadline = timeout or _DEFAULT_COMMUTATE_TIMEOUT
  retries = 0

  set_motor_state(engine, address, MotorState.COMMUTATE)
  start = monotonic()
  state = read_motor_state(engine, address)
  while state != MotorState.COMMUTATED:
    sleep(poll)
    elapsed = monotonic() - start
    if elapsed > deadline:
      raise BravoError(
        ErrorType.COULD_NOT_ALIGN,
        custom_text=f"Axis commutation timeout [{axis_name}]",
      )
    state = read_motor_state(engine, address)
    if state == MotorState.INITIAL:
      retries += 1
      if retries > _COMMUTATE_RETRIES:
        raise BravoError(
          ErrorType.COULD_NOT_ALIGN,
          custom_text=(f"Axis commutation failed after {_COMMUTATE_RETRIES} retries [{axis_name}]"),
        )
      set_motor_state(engine, address, MotorState.COMMUTATE)
      state = read_motor_state(engine, address)


# --- Home -----------------------------------------------------------------------


def home(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  *,
  timeout: Optional[float] = None,
  poll: float = _STATE_POLL,
  commutate_timeout: Optional[float] = None,
  get_estop_engaged: Callable[[], bool] = lambda: False,
) -> None:
  """Home the axis: reset the homing index, set state to Home, wait for Ready.

  If the post-Home state does not climb above ``HOME`` (indicating the
  homing sequence did not start cleanly), the sequence retries up to
  :data:`_HOMING_RETRIES` times, re-commutating between attempts.

  Requires the axis to be at least ``COMMUTATED`` before starting.

  Args:
    engine: The Gemini engine to drive the axis through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    timeout: Homing timeout for each attempt, in seconds; defaults to
      :data:`_DEFAULT_HOME_TIMEOUT`.
    poll: Delay between state polls, in seconds.
    commutate_timeout: Commutation timeout to use on a retry, in seconds.
    get_estop_engaged: Returns True if E-stop is engaged; forwarded to
      :func:`commutate` on a retry.

  Raises:
    BravoError: If the axis is not commutated, cannot be disabled while
      homed, the timeout elapses before reaching ``READY``, or homing
      retries are exhausted.
  """
  deadline = timeout or _DEFAULT_HOME_TIMEOUT

  for _attempt in range(_HOMING_RETRIES):
    state = read_motor_state(engine, address)
    if int(state) < int(MotorState.COMMUTATED):
      raise BravoError(
        ErrorType.NOT_HOMED,
        custom_text=f"Axis not commutated [{axis_name}]",
      )
    if state == MotorState.DISABLED:
      raise BravoError(
        ErrorType.COULD_NOT_HOME,
        custom_text=f"Motor cannot be disabled when homed [{axis_name}]",
      )

    engine.set_uint(address, GeminiSubCommands.HIDX_REC_DIST, 0)
    set_motor_state(engine, address, MotorState.HOME)

    start = monotonic()
    state = read_motor_state(engine, address)
    while int(state) >= int(MotorState.HOME) and int(state) < int(MotorState.READY):
      sleep(poll)
      elapsed = monotonic() - start
      if elapsed > deadline:
        raise BravoError(
          ErrorType.COULD_NOT_HOME,
          custom_text=f"Axis homing timeout [{axis_name}]",
        )
      state = read_motor_state(engine, address)

    if int(state) < int(MotorState.HOME):
      # State regressed below HOME (e.g. back to COMMUTATED) -- re-commutate
      # and retry.
      commutate(
        engine,
        address,
        axis_name,
        timeout=commutate_timeout,
        poll=poll,
        get_estop_engaged=get_estop_engaged,
      )
      continue
    return

  raise BravoError(
    ErrorType.COULD_NOT_HOME,
    custom_text=f"Axis homing retries exceeded [{axis_name}]",
  )


# --- Initialize (commutate + home) -----------------------------------------------


def is_initialized(engine: GeminiEngine, address: InstructionAddress, timeout: float = 5.0) -> bool:
  """Return whether the axis has been commutated and homed.

  Re-homing an already-initialized axis requires disabling it first, or the
  controller NAKs with ``MOVE_IN_PROGRESS``.

  Args:
    engine: The Gemini engine to read through.
    address: The axis device's controller-tree address.
    timeout: Maximum time to wait for the wire exchange, in seconds.

  Returns:
    True if the axis's motor state is at or beyond ``READY``.
  """
  state = read_motor_state(engine, address, timeout)
  return int(state) >= int(MotorState.READY)


def initialize(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  *,
  commutate_timeout: Optional[float] = None,
  home_timeout: Optional[float] = None,
  force: bool = False,
  get_estop_engaged: Callable[[], bool] = lambda: False,
) -> None:
  """Commutate and home the axis. Skips both if already initialized.

  An axis that reports itself initialized is left alone. Pass ``force=True``
  to home even an already-homed axis (requires disabling first).

  Args:
    engine: The Gemini engine to drive the axis through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    commutate_timeout: Commutation timeout, in seconds.
    home_timeout: Homing timeout, in seconds.
    force: Re-run commutation and homing even if the axis already reports
      itself initialized.
    get_estop_engaged: Returns True if E-stop is engaged; forwarded to
      :func:`commutate`.
  """
  if not force and is_initialized(engine, address):
    return
  if force:
    disable(engine, address, axis_name)
    # Give the controller a moment to honor the disable.
    sleep(0.05)
  commutate(
    engine,
    address,
    axis_name,
    timeout=commutate_timeout,
    get_estop_engaged=get_estop_engaged,
  )
  home(
    engine,
    address,
    axis_name,
    timeout=home_timeout,
    commutate_timeout=commutate_timeout,
    get_estop_engaged=get_estop_engaged,
  )


# --- Enable / disable ---------------------------------------------------------


def is_enabled(engine: GeminiEngine, address: InstructionAddress, timeout: float = 5.0) -> bool:
  """Return whether the axis's motor is currently enabled.

  Args:
    engine: The Gemini engine to read through.
    address: The axis device's controller-tree address.
    timeout: Maximum time to wait for the wire exchange, in seconds.

  Returns:
    True unless the axis's motor state is ``DISABLED``.
  """
  state = read_motor_state(engine, address, timeout)
  return state != MotorState.DISABLED


def enable(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  timeout: float = 5.0,
) -> None:
  """Transition the axis from DISABLED to READY. A no-op if already non-disabled.

  Args:
    engine: The Gemini engine to drive the axis through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    timeout: Maximum time to wait for the transition, in seconds; also used
      as the per-poll wire-exchange timeout.

  Raises:
    BravoError: If the axis does not leave the disable family of states
      within ``timeout``.
  """
  state = read_motor_state(engine, address, timeout)
  if state != MotorState.DISABLED:
    return
  set_motor_state(engine, address, MotorState.ENABLE, timeout)
  start = monotonic()
  while True:
    sleep(_STATE_POLL)
    state = read_motor_state(engine, address, timeout)
    if state not in (MotorState.DISABLED, MotorState.DISABLE, MotorState.ENABLE):
      return
    if monotonic() - start > timeout:
      raise BravoError(
        ErrorType.COULD_NOT_ENABLE_MOTOR,
        custom_text=f"Motor enable timeout [{axis_name}]",
      )


def disable(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,  # noqa: ARG001 - kept for a uniform axis-operation signature
  timeout: float = 5.0,
) -> None:
  """Transition the axis to DISABLED. Fire-and-forget; does not wait.

  Args:
    engine: The Gemini engine to drive the axis through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name; unused, kept so every axis
      operation shares the same signature shape.
    timeout: Maximum time to wait for the wire exchange, in seconds.
  """
  set_motor_state(engine, address, MotorState.DISABLE, timeout)


def reset_faults(engine: GeminiEngine, address: InstructionAddress, timeout: float = 5.0) -> None:
  """Clear axis fault state.

  A no-op on Darwin. Kept so callers do not have to special-case it.

  Args:
    engine: The Gemini engine (unused).
    address: The axis device's controller-tree address (unused).
    timeout: Unused.
  """
  del engine, address, timeout


__all__ = [
  "AxisTimeouts",
  "commutate",
  "disable",
  "enable",
  "home",
  "initialize",
  "is_enabled",
  "is_initialized",
  "read_motor_state",
  "reset_faults",
  "set_motor_state",
  "timeouts_for",
]
