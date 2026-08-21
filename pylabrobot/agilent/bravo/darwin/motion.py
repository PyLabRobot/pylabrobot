"""Motion primitives -- instruction loading and execution.

Each move on the Gemini controller is a 4-word :class:`~..protocol.gemini.instruction.Instruction`
loaded into the device's instruction table, armed with start/send event
numbers, and triggered by a broadcast ``TRIGGER`` with the start event. The
device writes back a ``TRIGGER`` with the send event when the move completes.

This polls ``MOTOR_STATE`` for BUSY -> READY transitions rather than wiring
event callbacks for that part.

Public API:
  :func:`build_load_packets`  -- construct the multipacket batch for one instruction
  :func:`load_instruction`    -- send the multipacket batch for one axis
  :func:`trigger_event`       -- broadcast TRIGGER with an event number
  :func:`wait_for_ready`      -- poll MOTOR_STATE until READY (or timeout)
  :func:`move_absolute`       -- single-axis absolute move, wait for completion
  :func:`move_multi`          -- multi-axis coordinated move with settle polling
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from ..errors import BravoError, ErrorType
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import (
  AxisDirection,
  CommandTypes,
  CommonSubCommands,
  GeminiSubCommands,
  InstructionTypes,
  MotorState,
  ReservedEvent,
)
from ..protocol.gemini.instruction import Instruction
from ..protocol.gemini.packet import BROADCAST_ADDRESS, HOST_ADDRESS, InstructionAddress, Packet
from .axis import read_motor_state

_DEFAULT_MOVE_TIMEOUT = 30.0
_DEFAULT_SETTLE_POLL = 0.01
# How long to insist on seeing BUSY before accepting READY as "move complete".
# On real hardware, the axis transitions to BUSY some time after the trigger
# broadcast arrives -- polling before then would see the pre-move READY and
# falsely declare the move done. BUSY must appear at least once within this
# window (fails with MOVE_TIMEOUT otherwise).
_BUSY_CONFIRM = 0.5


@dataclass
class LoadedMove:
  """One instruction queued on a specific axis.

  Attributes:
    address: The axis device's controller-tree address.
    instruction: The instruction loaded on that device.
    start_event: The event number that starts the instruction.
    send_event: The event number the device echoes on completion.
  """

  address: InstructionAddress
  instruction: Instruction
  start_event: int
  send_event: int


# --- Packet-list builders -----------------------------------------------------


def build_load_packets(
  address: InstructionAddress,
  instruction: Instruction,
  start_event: int,
  send_event: int,
) -> List[Packet]:
  """Return the SET packets that load one instruction on one axis.

  Sequence: ``INSTR_NEW_INSTR(1)`` -> 4x ``INSTR_TBL_VAL`` -> ``START_EVT``
  -> ``SEND_EVT`` -- the 7-packet pattern the firmware expects. The
  controller keeps its own instruction-slot state across moves, so
  ``INSTR_NEW_INSTR(1)`` alone is sufficient; an initial clear is not
  needed and breaks event binding.

  Args:
    address: The axis device's controller-tree address.
    instruction: The instruction to load.
    start_event: The event number that starts the instruction.
    send_event: The event number the device echoes on completion.

  Returns:
    The packets to send, in order.
  """
  w0, w1, w2, w3 = instruction.to_words()
  return [
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.INSTR_NEW_INSTR, 1),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.INSTR_TBL_VAL, w0),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.INSTR_TBL_VAL, w1),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.INSTR_TBL_VAL, w2),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.INSTR_TBL_VAL, w3),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.START_EVT, start_event),
    Packet(HOST_ADDRESS, address, CommandTypes.SETCMD, GeminiSubCommands.SEND_EVT, send_event),
  ]


def load_instruction(
  engine: GeminiEngine,
  address: InstructionAddress,
  instruction: Instruction,
  start_event: int,
  send_event: Optional[int] = None,
  timeout: float = 10.0,
) -> None:
  """Load one instruction onto one axis as a single multipacket.

  Args:
    engine: The Gemini engine to send through.
    address: The axis device's controller-tree address.
    instruction: The instruction to load.
    start_event: The event number that starts the instruction.
    send_event: The event number the device echoes on completion. If
      ``None``, uses the standard composite encoding from
      :func:`_compose_send_event`.
    timeout: Maximum time to wait for the multipacket response, in seconds.
  """
  if send_event is None:
    send_event = _compose_send_event(start_event)
  packets = build_load_packets(address, instruction, start_event, send_event)
  engine.send_multipacket(packets, timeout)


def load_instructions(
  engine: GeminiEngine,
  moves: List[LoadedMove],
  timeout: float = 10.0,
) -> None:
  """Batch-load N instructions (one per axis) as a single multipacket.

  The engine chunks into multiple multipackets if the total exceeds 64
  packets (each axis contributes 7 packets, so this would only trigger at
  10 axes -- never reached in practice, but handled safely).

  Args:
    engine: The Gemini engine to send through.
    moves: The per-axis instructions to load.
    timeout: Maximum time to wait for each chunk's response, in seconds.
  """
  packets: List[Packet] = []
  for m in moves:
    packets.extend(build_load_packets(m.address, m.instruction, m.start_event, m.send_event))
  engine.send_multipacket(packets, timeout)


# --- Triggering -----------------------------------------------------------------


def trigger_event(engine: GeminiEngine, event_number: int, timeout: float = 5.0) -> None:
  """Broadcast ``TRIGGER`` with an event number.

  Any axis whose ``START_EVT`` equals ``event_number`` begins executing its
  loaded instruction. Broadcasts do not wait for a response -- the engine
  returns after its broadcast wait interval.

  Args:
    engine: The Gemini engine to send through.
    event_number: The event number to broadcast.
    timeout: Ignored for a broadcast send; kept for a uniform signature with
      other wire operations.
  """
  engine.set_uint(BROADCAST_ADDRESS, CommonSubCommands.TRIGGER, event_number, timeout)


# --- Polling for completion -------------------------------------------------------


def wait_for_ready(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  *,
  timeout: float = _DEFAULT_MOVE_TIMEOUT,
  poll: float = _DEFAULT_SETTLE_POLL,
  busy_confirm: float = _BUSY_CONFIRM,
) -> MotorState:
  """Poll ``MOTOR_STATE`` until it returns to READY (or an error state).

  Must observe at least one ``BUSY`` reading before accepting ``READY`` as
  "move complete" -- otherwise this would race and return immediately on
  the pre-move READY state before the controller has transitioned. If BUSY
  is never observed within ``busy_confirm``, raises ``MOVE_TIMEOUT``.

  Args:
    engine: The Gemini engine to poll through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    timeout: Overall timeout for the move to complete, in seconds.
    poll: Delay between state polls, in seconds.
    busy_confirm: Window within which BUSY must first appear, in seconds.

  Returns:
    The axis's final motor state (``READY``).

  Raises:
    BravoError: If the axis is disabled during the move, never enters
      BUSY within ``busy_confirm``, or does not reach READY within
      ``timeout``.
  """
  start = time.monotonic()
  saw_busy = False
  while True:
    state = read_motor_state(engine, address)
    if state in (MotorState.BUSY, MotorState.MOVE_TO_FLAG, MotorState.MOVE_TO_INDEX):
      saw_busy = True
    elif state == MotorState.READY and saw_busy:
      return state
    elif state in (MotorState.DISABLED, MotorState.DISABLE):
      raise BravoError(
        ErrorType.MOTOR_POWER,
        custom_text=f"Axis disabled during move [{axis_name}]",
      )

    elapsed = time.monotonic() - start
    if not saw_busy and elapsed > busy_confirm:
      raise BravoError(
        ErrorType.MOVE_TIMEOUT,
        custom_text=(
          f"Axis never entered BUSY within {busy_confirm}s "
          f"[{axis_name}] -- trigger may not have been received"
        ),
      )
    if elapsed > timeout:
      raise BravoError(
        ErrorType.MOVE_TIMEOUT,
        custom_text=f"Move timeout waiting for READY [{axis_name}]",
      )
    time.sleep(poll)


def wait_for_all_ready(
  engine: GeminiEngine,
  moves: List[LoadedMove],
  axis_names: Dict[int, str],
  *,
  timeout: float = _DEFAULT_MOVE_TIMEOUT,
  poll: float = _DEFAULT_SETTLE_POLL,
  busy_confirm: float = _BUSY_CONFIRM,
) -> None:
  """Poll all loaded axes until every one is READY.

  Each axis must be observed in BUSY at least once before its READY state
  counts as "move complete" -- see :func:`wait_for_ready` for the rationale.

  Args:
    engine: The Gemini engine to poll through.
    moves: The loaded moves whose axes to wait on.
    axis_names: Display names for each axis, keyed by address byte, used in
      error messages.
    timeout: Overall timeout for every axis to complete, in seconds.
    poll: Delay between polling rounds, in seconds.
    busy_confirm: Window within which every axis must first appear BUSY, in
      seconds.

  Raises:
    BravoError: If any axis is disabled during the move, any axis never
      enters BUSY within ``busy_confirm``, or not every axis reaches READY
      within ``timeout``.
  """
  start = time.monotonic()
  remaining = {m.address.byte: m.address for m in moves}
  saw_busy: Set[int] = set()
  while remaining:
    for addr_byte, addr in list(remaining.items()):
      state = read_motor_state(engine, addr)
      name = axis_names.get(addr_byte, str(addr))
      if state in (MotorState.BUSY, MotorState.MOVE_TO_FLAG, MotorState.MOVE_TO_INDEX):
        saw_busy.add(addr_byte)
      elif state == MotorState.READY and addr_byte in saw_busy:
        del remaining[addr_byte]
      elif state in (MotorState.DISABLED, MotorState.DISABLE):
        raise BravoError(
          ErrorType.MOTOR_POWER,
          custom_text=f"Axis disabled during move [{name}]",
        )

    elapsed = time.monotonic() - start

    if remaining and elapsed > busy_confirm:
      missing = [axis_names.get(b, str(a)) for b, a in remaining.items() if b not in saw_busy]
      if missing:
        raise BravoError(
          ErrorType.MOVE_TIMEOUT,
          custom_text=(f"Axes never entered BUSY within {busy_confirm}s: {', '.join(missing)}"),
        )

    if remaining and elapsed > timeout:
      names = ", ".join(axis_names.get(a.byte, str(a)) for a in remaining.values())
      raise BravoError(
        ErrorType.MOVE_TIMEOUT,
        custom_text=f"Multi-axis move timeout; still busy: {names}",
      )
    if remaining:
      time.sleep(poll)


# --- High-level entry points ---------------------------------------------------


def _make_move_instruction(
  target_normalized: float,
  *,
  instr_type: InstructionTypes = InstructionTypes.MOVE_TO,
  velocity_percent: float = 100.0,
  acceleration_percent: float = 100.0,
  jerk_percent: float = 100.0,
  force_percent: float = 0.0,
  direction: AxisDirection = AxisDirection.POSITIVE,
  trig_at_normalized: Optional[float] = None,
) -> Instruction:
  """Build a MOVE_TO/MOVE_BY instruction targeting a normalized position.

  Args:
    target_normalized: The target position or volume, in normalized [0, 1]
      axis units.
    instr_type: The instruction type.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    jerk_percent: Move jerk, 0-100% of axis max.
    force_percent: Force limit, 0-100%.
    direction: Move direction.
    trig_at_normalized: The trigger position, in normalized units. Defaults
      to ``target_normalized`` -- a real MoveAbsolute instruction sets word3
      equal to word2, and firing the SEND event depends on the axis
      reaching this trigger position.

  Returns:
    The built instruction.
  """
  inst = Instruction(
    instr_type=instr_type,
    velocity_percent=velocity_percent,
    acceleration_percent=acceleration_percent,
    jerk_percent=jerk_percent,
    force_percent=force_percent,
    direction=direction,
  )
  inst.volume = target_normalized
  inst.trig_at_float = trig_at_normalized if trig_at_normalized is not None else target_normalized
  return inst


def _compose_send_event(start_event: int) -> int:
  """Encode the SEND_EVT value used in real instructions.

  SEND_EVT is always a composite instruction event with mask=1 and
  event_no=start_event+1, encoded as::

    evt = (mask << 8) | 0x80 | (event_no & 0x7F)

  Args:
    start_event: The instruction's start event number.

  Returns:
    The composite send-event value.
  """
  event_no = (start_event + 1) & 0x7F
  return (1 << 8) | 0x80 | event_no


class _MoveWaiter:
  """Context manager to wait for SEND_EVT echoes or a RESERVED error.

  The firmware signals move completion by broadcasting the SEND_EVT value
  (e.g. 0x182) from EACH axis as it finishes -- one echo per axis. For a
  multi-axis coordinated move, all axes share the same send_event but
  complete at different times, so a correct completion condition is "an
  echo has been seen from EVERY expected source, not just the first one".
  Stopping at the first echo would let the caller advance to the next step
  while slower axes are still in motion.

  Construction modes:

  - ``expected_src`` (single): wait for exactly one echo from that
    address. Used by every single-axis primitive (:func:`move_absolute`,
    :func:`move_relative`, ``force_move``, ``grip``).
  - ``expected_srcs`` (set): wait for one echo from EACH address in the
    set. Used by :func:`move_multi` -- the set contains every axis in the
    coordinated move.
  - Neither provided: accept any source (legacy fallback; any single
    matching echo resolves the wait).

  Exactly one of ``expected_src``/``expected_srcs`` should be provided.
  """

  def __init__(
    self,
    engine: GeminiEngine,
    send_event: int,
    label: str,
    expected_src: Optional[InstructionAddress] = None,
    expected_srcs: Optional[Set[InstructionAddress]] = None,
  ):
    """Set up a waiter for one or more SEND_EVT echoes.

    Args:
      engine: The Gemini engine whose trigger/reserved-event callbacks to
        subscribe to.
      send_event: The composite event value to wait for.
      label: A description of the move, used in timeout error messages.
      expected_src: The single source address to wait on.
      expected_srcs: The set of source addresses to wait on, one echo each.

    Raises:
      ValueError: If both ``expected_src`` and ``expected_srcs`` are given.
    """
    if expected_src is not None and expected_srcs is not None:
      raise ValueError("Pass only one of expected_src / expected_srcs to _MoveWaiter.")
    self._engine = engine
    self._send_event = send_event
    self._label = label
    self._lock = threading.Lock()
    self._pending: Optional[Set[InstructionAddress]]
    if expected_src is not None:
      self._pending = {expected_src}
    elif expected_srcs is not None:
      self._pending = set(expected_srcs)
    else:
      self._pending = None
    self._done = threading.Event()
    self._reserved: Optional[ReservedEvent] = None
    self._reserved_src: Optional[tuple] = None

  def __enter__(self) -> "_MoveWaiter":
    """Subscribe this waiter's callbacks and return it."""
    self._engine.on_trigger(self._on_trigger)
    self._engine.on_reserved_event(self._on_reserved)
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    """Unsubscribe the trigger callback.

    The reserved-event callback is left registered: the engine has no
    ``remove_reserved_event`` hook, and the callback only sets this
    instance's own event, which a later waiter on a different instance
    never observes -- harmless.
    """
    self._engine.remove_trigger(self._on_trigger)

  def _on_trigger(self, pkt: Packet) -> None:
    """Resolve the wait when a matching SEND_EVT echo arrives.

    Args:
      pkt: The received trigger packet.
    """
    if pkt.cmd_val != self._send_event:
      return
    with self._lock:
      if self._pending is None:
        self._done.set()
        return
      if pkt.src not in self._pending:
        # Ignore echoes from axes not being tracked (e.g. stale broadcasts
        # from a previously-completed move elsewhere in the controller
        # tree).
        return
      self._pending.discard(pkt.src)
      if not self._pending:
        self._done.set()

  def _on_reserved(self, reserved: ReservedEvent, pkt: Packet) -> None:
    """Resolve the wait with a recorded error when a RESERVED event arrives.

    Args:
      reserved: The decoded reserved event.
      pkt: The packet the event arrived in.
    """
    self._reserved = reserved
    self._reserved_src = (pkt.src.node_id, pkt.src.dev_id)
    self._done.set()

  def wait(self, timeout: float) -> None:
    """Block until every expected echo (or a RESERVED event) arrives.

    Args:
      timeout: Maximum time to wait, in seconds.

    Raises:
      BravoError: If no matching echo arrives within ``timeout``, or a
        RESERVED event aborted the move.
    """
    if not self._done.wait(timeout):
      raise BravoError(
        ErrorType.MOVE_TIMEOUT,
        custom_text=(
          f"Move timeout [{self._label}]: no SEND_EVT echo "
          f"(0x{self._send_event:x}) within {timeout}s"
        ),
      )
    if self._reserved is not None:
      src = self._reserved_src or (0, 0)
      err_map = {
        ReservedEvent.STOP: ErrorType.STOP_COMMAND,
        ReservedEvent.ERROR: ErrorType.CONTROLLER_INTERNAL,
        ReservedEvent.FAULT: ErrorType.CONTROLLER_FATAL,
        ReservedEvent.STOP_DISABLE: ErrorType.ROBOT_DISABLE,
        ReservedEvent.SAFETY_NOTICE: ErrorType.ROBOT_DISABLE,
      }
      err_type = err_map.get(self._reserved, ErrorType.DARWIN_GENERIC)
      raise BravoError(
        err_type,
        custom_text=(
          f"Move aborted [{self._label}]: controller broadcast "
          f"RESERVED event {self._reserved.name} from node {src[0]}.{src[1]}"
        ),
      )


def move_absolute(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  target_normalized: float,
  *,
  velocity_percent: float = 100.0,
  acceleration_percent: float = 100.0,
  wait: bool = True,
  start_event: int = 1,
  timeout: float = _DEFAULT_MOVE_TIMEOUT,
) -> None:
  """Move to an absolute target, in normalized axis units.

  Normalized units are the float-in-word-2 form the controller expects --
  the caller is responsible for converting mm or uL to normalized.

  Args:
    engine: The Gemini engine to drive the move through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    target_normalized: The absolute target, in normalized [0, 1] axis
      units.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    wait: Whether to block until the move finishes.
    start_event: The event number to start the instruction with.
    timeout: Maximum time to wait for completion, in seconds.
  """
  inst = _make_move_instruction(
    target_normalized,
    instr_type=InstructionTypes.MOVE_TO,
    velocity_percent=velocity_percent,
    acceleration_percent=acceleration_percent,
  )
  send_event = _compose_send_event(start_event)
  if wait:
    # Wait for either the SEND_EVT echo or a RESERVED event (which signals
    # the move was aborted by an error/safety condition).
    with _MoveWaiter(engine, send_event, axis_name, expected_src=address) as waiter:
      load_instruction(engine, address, inst, start_event, send_event)
      trigger_event(engine, start_event)
      waiter.wait(timeout)
  else:
    load_instruction(engine, address, inst, start_event, send_event)
    trigger_event(engine, start_event)


def move_relative(
  engine: GeminiEngine,
  address: InstructionAddress,
  axis_name: str,
  delta_normalized: float,
  *,
  direction: AxisDirection = AxisDirection.POSITIVE,
  velocity_percent: float = 100.0,
  acceleration_percent: float = 100.0,
  wait: bool = True,
  start_event: int = 1,
  timeout: float = _DEFAULT_MOVE_TIMEOUT,
) -> None:
  """Move by ``delta_normalized`` in the given direction.

  Args:
    engine: The Gemini engine to drive the move through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    delta_normalized: The move distance, in normalized axis units
      (magnitude only -- sign comes from ``direction``).
    direction: Move direction.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    wait: Whether to block until the move finishes.
    start_event: The event number to start the instruction with.
    timeout: Maximum time to wait for completion, in seconds.
  """
  inst = _make_move_instruction(
    abs(delta_normalized),
    instr_type=InstructionTypes.MOVE_BY,
    velocity_percent=velocity_percent,
    acceleration_percent=acceleration_percent,
    direction=direction,
  )
  send_event = _compose_send_event(start_event)
  if wait:
    with _MoveWaiter(engine, send_event, axis_name, expected_src=address) as waiter:
      load_instruction(engine, address, inst, start_event, send_event)
      trigger_event(engine, start_event)
      waiter.wait(timeout)
  else:
    load_instruction(engine, address, inst, start_event, send_event)
    trigger_event(engine, start_event)


@dataclass
class MoveRequest:
  """One axis's contribution to a coordinated multi-axis move.

  Attributes:
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    target_normalized: The absolute target, in normalized [0, 1] axis
      units.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    instr_type: The instruction type.
    direction: Move direction.
  """

  address: InstructionAddress
  axis_name: str
  target_normalized: float
  velocity_percent: float = 100.0
  acceleration_percent: float = 100.0
  instr_type: InstructionTypes = InstructionTypes.MOVE_TO
  direction: AxisDirection = AxisDirection.POSITIVE


def move_multi(
  engine: GeminiEngine,
  requests: List[MoveRequest],
  *,
  wait: bool = True,
  start_event: int = 1,
  timeout: float = _DEFAULT_MOVE_TIMEOUT,
) -> None:
  """Coordinated multi-axis move -- all axes triggered by the same start event.

  With a coordinated move all axes share the same SEND_EVT, so a single
  echo broadcast signals completion for all of them.

  Args:
    engine: The Gemini engine to drive the move through.
    requests: The per-axis targets to move to together.
    wait: Whether to block until every axis's move finishes.
    start_event: The event number to start every instruction with.
    timeout: Maximum time to wait for completion, in seconds.
  """
  if not requests:
    return
  send_event = _compose_send_event(start_event)
  moves: List[LoadedMove] = []
  axis_names: Dict[int, str] = {}
  for req in requests:
    inst = _make_move_instruction(
      req.target_normalized,
      instr_type=req.instr_type,
      velocity_percent=req.velocity_percent,
      acceleration_percent=req.acceleration_percent,
      direction=req.direction,
    )
    moves.append(
      LoadedMove(
        address=req.address,
        instruction=inst,
        start_event=start_event,
        send_event=send_event,
      )
    )
    axis_names[req.address.byte] = req.axis_name

  if wait:
    label = ", ".join(axis_names.values())
    # Wait for an echo from EVERY axis in the coordinated move, not just the
    # first. Each axis broadcasts SEND_EVT independently when it reaches its
    # target; resolving on the first echo would let the caller advance to
    # the next step while slower axes are still in motion, causing a
    # subsequent move (e.g. a grip) to fire into a machine that has not
    # finished the current one.
    expected_srcs = {req.address for req in requests}
    with _MoveWaiter(engine, send_event, label, expected_srcs=expected_srcs) as waiter:
      load_instructions(engine, moves, timeout)
      trigger_event(engine, start_event)
      waiter.wait(timeout)
  else:
    load_instructions(engine, moves, timeout)
    trigger_event(engine, start_event)
