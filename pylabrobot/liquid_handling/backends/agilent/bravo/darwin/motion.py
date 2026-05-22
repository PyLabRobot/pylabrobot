"""Motion primitives — instruction loading and execution.

Each move on the Gemini controller is a 4-word Instruction loaded into the
device's instruction table, armed with start/send event numbers, and triggered
by a broadcast ``SUBCMD_TRIGGER`` with the start event. The device writes back
a ``SUBCMD_TRIGGER`` with the send event when the move completes.

This v1 polls ``MOTOR_STATE`` for BUSY→READY transitions rather than wiring
event callbacks. Matches the bridge's approach for single-axis moves.

Public API:
    :func:`build_load_packets`  — construct the multipacket batch for one instruction
    :func:`load_instruction`    — send the multipacket batch for one axis
    :func:`trigger_event`       — broadcast SUBCMD_TRIGGER with an event number
    :func:`wait_for_ready`      — poll MOTOR_STATE until READY (or timeout)
    :func:`move_absolute`       — single-axis absolute move, wait for completion
    :func:`move_multi`          — multi-axis coordinated move with settle polling
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.axis import read_motor_state
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.engine import GeminiEngine
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
    EVENT_RESERVED,
    AxisDirection,
    CommandTypes,
    CommonSubCommands,
    GeminiSubCommands,
    InstructionTypes,
    MotorState,
    ReservedEvent,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.instruction import Instruction, pack_float32
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import (
    BROADCAST_ADDRESS,
    HOST_ADDRESS,
    InstructionAddress,
    Packet,
)


_DEFAULT_MOVE_TIMEOUT_MS = 30_000
_DEFAULT_SETTLE_POLL_MS = 10
# How long to insist on seeing BUSY before accepting READY as "move complete".
# On real hardware, the axis transitions to BUSY some ms after the trigger
# broadcast arrives — if we poll before then, we'll see the pre-move READY
# and falsely declare the move done. Require BUSY to appear at least once
# within this window (fails with MOVE_TIMEOUT otherwise).
_BUSY_CONFIRM_MS = 500


@dataclass
class LoadedMove:
    """One instruction queued on a specific axis."""

    address: InstructionAddress
    instruction: Instruction
    start_event: int
    send_event: int


# --- Packet-list builders ---------------------------------------------------


def build_load_packets(
    address: InstructionAddress,
    instruction: Instruction,
    start_event: int,
    send_event: int,
) -> list[Packet]:
    """Return the sequence of SET packets that load one instruction on one axis.

    Sequence: INSTR_NEW_INSTR(1) → 4×INSTR_TBL_VAL → START_EVT → SEND_EVT.
    Matches the 7-packet pattern observed in the 4to2Looping pcap. (An initial
    INSTR_CLEAR was tried here but broke event binding — the controller keeps
    its own instruction-slot state across moves; NEW_INSTR(1) is sufficient.)
    """
    w0, w1, w2, w3 = instruction.to_words()
    return [
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.INSTR_NEW_INSTR, 1),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.INSTR_TBL_VAL, w0),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.INSTR_TBL_VAL, w1),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.INSTR_TBL_VAL, w2),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.INSTR_TBL_VAL, w3),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.START_EVT, start_event),
        Packet(HOST_ADDRESS, address, CommandTypes.SETCMD,
               GeminiSubCommands.SEND_EVT, send_event),
    ]


def load_instruction(
    engine: GeminiEngine,
    address: InstructionAddress,
    instruction: Instruction,
    start_event: int,
    send_event: int | None = None,
    timeout_ms: int = 10_000,
) -> None:
    """Single-axis instruction load as one multipacket.

    If ``send_event`` is None, uses the standard composite encoding
    ``(1<<8) | 0x80 | (start_event+1)`` observed in the 4to2Looping pcap.
    """
    if send_event is None:
        send_event = _compose_send_event(start_event)
    packets = build_load_packets(address, instruction, start_event, send_event)
    engine.send_multipacket(packets, timeout_ms=timeout_ms)


def load_instructions(
    engine: GeminiEngine,
    moves: list[LoadedMove],
    timeout_ms: int = 10_000,
) -> None:
    """Batch-load N instructions (one per axis) as a single multipacket.

    The engine will chunk into multiple multipackets if the total exceeds 64
    packets (each axis contributes 8 packets so this would kick in at 9 axes —
    never triggered in practice, but safe).
    """
    packets: list[Packet] = []
    for m in moves:
        packets.extend(
            build_load_packets(m.address, m.instruction, m.start_event, m.send_event)
        )
    engine.send_multipacket(packets, timeout_ms=timeout_ms)


# --- Triggering -------------------------------------------------------------


def trigger_event(
    engine: GeminiEngine, event_number: int, timeout_ms: int = 5000
) -> None:
    """Broadcast ``SUBCMD_TRIGGER`` with an event number.

    Any axis whose ``START_EVT`` equals ``event_number`` begins executing its
    loaded instruction. Broadcasts don't wait for a response — the engine
    returns after ``BROADCAST_WAIT_MS``.
    """
    engine.set_uint(
        BROADCAST_ADDRESS,
        CommonSubCommands.TRIGGER,
        event_number,
        timeout_ms=timeout_ms,
    )


# --- Polling for completion -------------------------------------------------


def wait_for_ready(
    engine: GeminiEngine,
    address: InstructionAddress,
    axis_name: str,
    *,
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
    poll_ms: int = _DEFAULT_SETTLE_POLL_MS,
    busy_confirm_ms: int = _BUSY_CONFIRM_MS,
) -> MotorState:
    """Poll ``MOTOR_STATE`` until it returns to READY (or an error state).

    Must observe at least one ``BUSY`` reading before accepting ``READY`` as
    "move complete" — otherwise we'd race and return immediately on the
    pre-move READY state before the controller has transitioned. If BUSY is
    never observed within ``busy_confirm_ms``, raises MOVE_TIMEOUT.
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

        elapsed_ms = (time.monotonic() - start) * 1000
        if not saw_busy and elapsed_ms > busy_confirm_ms:
            raise BravoError(
                ErrorType.MOVE_TIMEOUT,
                custom_text=(
                    f"Axis never entered BUSY within {busy_confirm_ms}ms "
                    f"[{axis_name}] — trigger may not have been received"
                ),
            )
        if elapsed_ms > timeout_ms:
            raise BravoError(
                ErrorType.MOVE_TIMEOUT,
                custom_text=f"Move timeout waiting for READY [{axis_name}]",
            )
        time.sleep(poll_ms / 1000.0)


def wait_for_all_ready(
    engine: GeminiEngine,
    moves: list[LoadedMove],
    axis_names: dict[int, str],
    *,
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
    poll_ms: int = _DEFAULT_SETTLE_POLL_MS,
    busy_confirm_ms: int = _BUSY_CONFIRM_MS,
) -> None:
    """Poll all loaded axes until every one is READY.

    Each axis must be observed in BUSY at least once before its READY state
    counts as "move complete" — see :func:`wait_for_ready` for the rationale.
    """
    start = time.monotonic()
    remaining = {m.address.byte: m.address for m in moves}
    saw_busy: set[int] = set()
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

        elapsed_ms = (time.monotonic() - start) * 1000

        # Fail if any axis hasn't transitioned to BUSY within the confirmation window
        if remaining and elapsed_ms > busy_confirm_ms:
            missing = [
                axis_names.get(b, str(a))
                for b, a in remaining.items()
                if b not in saw_busy
            ]
            if missing:
                raise BravoError(
                    ErrorType.MOVE_TIMEOUT,
                    custom_text=(
                        f"Axes never entered BUSY within {busy_confirm_ms}ms: "
                        f"{', '.join(missing)}"
                    ),
                )

        if remaining and elapsed_ms > timeout_ms:
            names = ", ".join(
                axis_names.get(a.byte, str(a)) for a in remaining.values()
            )
            raise BravoError(
                ErrorType.MOVE_TIMEOUT,
                custom_text=f"Multi-axis move timeout; still busy: {names}",
            )
        if remaining:
            time.sleep(poll_ms / 1000.0)


# --- High-level entry points -----------------------------------------------


def _make_move_instruction(
    target_normalized: float,
    *,
    instr_type: InstructionTypes = InstructionTypes.MOVE_TO,
    velocity_percent: float = 100.0,
    acceleration_percent: float = 100.0,
    jerk_percent: float = 100.0,
    force_percent: float = 0.0,
    direction: AxisDirection = AxisDirection.POSITIVE,
    trig_at_normalized: float | None = None,
) -> Instruction:
    inst = Instruction(
        instr_type=instr_type,
        velocity_percent=velocity_percent,
        acceleration_percent=acceleration_percent,
        jerk_percent=jerk_percent,
        force_percent=force_percent,
        direction=direction,
    )
    inst.volume = target_normalized
    # word3 (trig_at_value) defaults to the target position — pcap confirms
    # real-world MoveAbsolute instructions set word3 == word2. Firing the SEND
    # event depends on the axis reaching this trigger position.
    inst.trig_at_float = (
        trig_at_normalized if trig_at_normalized is not None else target_normalized
    )
    return inst


def _compose_send_event(start_event: int) -> int:
    """Encode the SEND_EVT value used in real instructions.

    From the 4to2Looping pcap: SEND_EVT is always a composite InstructionEvent
    with mask=1 and event_no=start_event+1. Encoding (from
    ``InstructionEvent.cs`` line 113):
        evt = (mask << 8) | 0x80 | (event_no & 0x7F)
    """
    event_no = (start_event + 1) & 0x7F
    return (1 << 8) | 0x80 | event_no


class _MoveWaiter:
    """Context manager to wait for SEND_EVT echoes or a RESERVED error.

    The firmware signals move completion by broadcasting the SEND_EVT value
    (e.g. 0x182) from EACH axis as it finishes — one echo per axis. For a
    multi-axis coordinated move, all axes share the same send_event but
    complete at different times, so a correct completion condition is
    "I have seen an echo from EVERY expected source, not just the first
    one." If we stop at the first echo, the Python layer returns while
    slower axes are still in motion and the next task step fires into a
    still-moving machine.

    Construction modes:

    - ``expected_src`` (single): wait for exactly one echo from that
      address. Used by every single-axis primitive (``move_absolute``,
      ``move_relative``, ``force_move``, ``grip``).
    - ``expected_srcs`` (set): wait for one echo from EACH address in the
      set. Used by ``move_multi`` — the set contains every axis in the
      coordinated move.
    - Neither provided: accept any source (legacy fallback; any single
      matching echo resolves the wait).

    Exactly one of ``expected_src`` / ``expected_srcs`` should be provided.

    Usage::

        with _MoveWaiter(engine, send_event, "X,Y",
                         expected_srcs={x_addr, y_addr}) as w:
            load_instructions(engine, moves, ...)
            trigger_event(engine, start_event)
            w.wait(timeout_ms)   # returns only after BOTH X and Y echo
    """

    def __init__(
        self,
        engine: GeminiEngine,
        send_event: int,
        label: str,
        expected_src: InstructionAddress | None = None,
        expected_srcs: set[InstructionAddress] | None = None,
    ):
        if expected_src is not None and expected_srcs is not None:
            raise ValueError(
                "Pass only one of expected_src / expected_srcs to _MoveWaiter."
            )
        self._engine = engine
        self._send_event = send_event
        self._label = label
        self._lock = threading.Lock()
        # Normalize to a set of pending source addresses. None → any-source
        # (legacy): first matching echo sets _pending empty via the None path.
        if expected_src is not None:
            self._pending: set[InstructionAddress] | None = {expected_src}
        elif expected_srcs is not None:
            self._pending = set(expected_srcs)
        else:
            self._pending = None
        self._done = threading.Event()
        self._reserved: ReservedEvent | None = None
        self._reserved_src: tuple[int, int] | None = None

    def __enter__(self) -> "_MoveWaiter":
        self._engine.on_trigger(self._on_trigger)
        self._engine.on_reserved_event(self._on_reserved)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._engine.remove_trigger(self._on_trigger)
        # Note: engine doesn't have remove_reserved_event; we leave the
        # callback registered. It only sets self._done which a new waiter
        # on a different instance won't see. Harmless.

    def _on_trigger(self, pkt: Packet) -> None:
        if pkt.cmd_val != self._send_event:
            return
        with self._lock:
            if self._pending is None:
                # Legacy any-source mode — first echo resolves.
                self._done.set()
                return
            if pkt.src not in self._pending:
                # Ignore echoes from axes we aren't tracking (e.g. stale
                # broadcasts from a previously-completed move elsewhere in
                # the controller tree).
                return
            self._pending.discard(pkt.src)
            if not self._pending:
                self._done.set()

    def _on_reserved(self, reserved: ReservedEvent, pkt: Packet) -> None:
        self._reserved = reserved
        self._reserved_src = (pkt.src.node_id, pkt.src.dev_id)
        self._done.set()

    def wait(self, timeout_ms: int) -> None:
        if not self._done.wait(timeout_ms / 1000.0):
            raise BravoError(
                ErrorType.MOVE_TIMEOUT,
                custom_text=(
                    f"Move timeout [{self._label}]: no SEND_EVT echo "
                    f"(0x{self._send_event:x}) within {timeout_ms}ms"
                ),
            )
        if self._reserved is not None:
            src = self._reserved_src or (0, 0)
            # Map the reserved event to an ErrorType that hints at the cause
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
                    f"RESERVED event {self._reserved.name} from node "
                    f"{src[0]}.{src[1]}"
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
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
) -> None:
    """Move to an absolute target (in normalized axis units).

    Normalized units are the float-in-word-2 form the controller expects —
    the caller is responsible for converting mm or µL to normalized.
    """
    inst = _make_move_instruction(
        target_normalized,
        instr_type=InstructionTypes.MOVE_TO,
        velocity_percent=velocity_percent,
        acceleration_percent=acceleration_percent,
    )
    send_event = _compose_send_event(start_event)
    if wait:
        # Wait for either the SEND_EVT echo or a RESERVED event (which
        # signals the move was aborted by an error/safety condition).
        with _MoveWaiter(engine, send_event, axis_name, expected_src=address) as waiter:
            load_instruction(engine, address, inst, start_event, send_event)
            trigger_event(engine, start_event)
            waiter.wait(timeout_ms)
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
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
) -> None:
    """Move by ``delta_normalized`` in the given direction."""
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
            waiter.wait(timeout_ms)
    else:
        load_instruction(engine, address, inst, start_event, send_event)
        trigger_event(engine, start_event)


@dataclass
class MoveRequest:
    """One axis's contribution to a coordinated multi-axis move."""

    address: InstructionAddress
    axis_name: str
    target_normalized: float
    velocity_percent: float = 100.0
    acceleration_percent: float = 100.0
    instr_type: InstructionTypes = InstructionTypes.MOVE_TO
    direction: AxisDirection = AxisDirection.POSITIVE


def move_multi(
    engine: GeminiEngine,
    requests: list[MoveRequest],
    *,
    wait: bool = True,
    start_event: int = 1,
    timeout_ms: int = _DEFAULT_MOVE_TIMEOUT_MS,
) -> None:
    """Coordinated multi-axis move — all axes triggered by the same start event.

    With a coordinated move all axes share the same SEND_EVT, so a single echo
    broadcast signals completion for all of them.
    """
    if not requests:
        return
    send_event = _compose_send_event(start_event)
    moves: list[LoadedMove] = []
    axis_names: dict[int, str] = {}
    for req in requests:
        inst = _make_move_instruction(
            req.target_normalized,
            instr_type=req.instr_type,
            velocity_percent=req.velocity_percent,
            acceleration_percent=req.acceleration_percent,
            direction=req.direction,
        )
        moves.append(LoadedMove(
            address=req.address, instruction=inst,
            start_event=start_event, send_event=send_event,
        ))
        axis_names[req.address.byte] = req.axis_name

    if wait:
        label = ", ".join(axis_names.values())
        # CRITICAL: wait for an echo from EVERY axis in the coordinated move,
        # not just the first. Each axis broadcasts SEND_EVT independently when
        # it reaches its target; resolving on the first echo would let the
        # caller advance to the next step while slower axes are still in
        # motion, causing the task-step sequencer to fire subsequent moves
        # (e.g. grip) into a machine that hasn't finished the current one.
        expected_srcs = {req.address for req in requests}
        with _MoveWaiter(
            engine, send_event, label, expected_srcs=expected_srcs,
        ) as waiter:
            load_instructions(engine, moves, timeout_ms=timeout_ms)
            trigger_event(engine, start_event)
            waiter.wait(timeout_ms)
    else:
        load_instructions(engine, moves, timeout_ms=timeout_ms)
        trigger_event(engine, start_event)
