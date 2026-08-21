"""GeminiEngine -- synchronous GET/SET/multipacket dispatcher for a Darwin controller.

Every command is issued under a single lock with one shared "response
complete" event, so at most one request is ever outstanding at a time. A
background thread continuously reads frames off the transport and dispatches
them: a GET/SET response wakes the waiting caller, while an unsolicited
trigger, stream, or reserved-event frame is fanned out to registered
callbacks.

Usage::

    transport = SocketTransport("bravo", "192.168.0.8", TCP_PORT)
    # transport.setup() is awaited by the caller before engine use begins.
    engine = GeminiEngine(transport)
    engine.start_receiving()
    try:
      fw = engine.get_value(InstructionAddress(4), CommonSubCommands.FW_VERSION)
    finally:
      engine.stop_receiving()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from pylabrobot.io import LOG_LEVEL_IO

from ...transport import Transport
from .enums import (
  BROADCAST_WAIT_MS,
  FRAME_HEADER_SIZE,
  MAX_PACKETS_PER_MULTIPACKET,
  NODE_BROADCAST,
  CommandTypes,
  CommonSubCommands,
  GeminiSubCommands,
  ReservedEvent,
  TCPMessageType,
  is_reserved_event,
)
from .errors import GeminiTimeoutError, MultipacketError, NAKError
from .framing import (
  FrameHeader,
  MultipacketResponse,
  pack_multipacket_frame,
  pack_packet_frame,
  pack_serial_frame,
)
from .instruction import pack_float32, unpack_float32
from .packet import MASTER_ADDRESS, InstructionAddress, Packet

logger = logging.getLogger(__name__)


# Short blocking read for the rx thread, so it can poll the stop flag
# frequently without busy-waiting.
_RX_POLL_S = 0.1
# How long close() gives the rx thread to notice the stop flag and exit.
_RX_STOP_JOIN_S = 2.0


PacketCallback = Callable[[Packet], None]
ReservedEventCallback = Callable[[ReservedEvent, Packet], None]


class GeminiEngine:
  """Synchronous get/set/multipacket dispatcher with a background rx thread.

  Threading model:

  - ``_command_lock`` serializes every command-issuing method so only one
    request is in flight at a time.
  - ``_command_complete`` is a single event, reset before each send and set
    by the rx thread when a response of matching shape arrives.
  - Shared response state (``_value_buffer``, ``_nak_response``, etc.) is
    written by the rx thread and read by the caller under the command lock.
  - ``msg_id`` in the packet is not used for correlation: the command lock
    already guarantees there is only ever one outstanding request.
  """

  def __init__(self, transport: Transport):
    """Bind this engine to a transport.

    Args:
      transport: The byte channel to the Darwin controller. Its connection
        lifecycle belongs to the caller: this engine's :meth:`connect` and
        :meth:`close` only start and stop the background receive thread, not
        the transport's own connection.
    """
    self._transport = transport
    self._command_lock = threading.Lock()
    self._command_complete = threading.Event()

    # Response state (written by rx thread, read under the command lock).
    self._value_buffer: int = 0
    self._nak_response: int = 0
    self._multipacket_success: bool = False
    self._multipacket_error_device: int = 0
    self._serial_response: Optional[bytes] = None

    self._rx_stop = threading.Event()
    self._rx_thread: Optional[threading.Thread] = None

    # Self-routed broadcast trigger queue, so a broadcast this engine itself
    # sends also reaches its own trigger callbacks.
    self._local_queue: deque = deque()
    self._local_queue_lock = threading.Lock()

    # Callbacks for unsolicited / stream packets.
    self._on_trigger_callbacks: list[PacketCallback] = []
    self._on_stream_callbacks: list[PacketCallback] = []
    self._on_reserved_event_callbacks: list[ReservedEventCallback] = []

  # --- Lifecycle ----------------------------------------------------------

  @property
  def is_connected(self) -> bool:
    """Whether the transport is connected and the receive thread is running."""
    return (
      self._transport.is_connected and self._rx_thread is not None and self._rx_thread.is_alive()
    )

  def start_receiving(self) -> None:
    """Start the background thread that receives and dispatches frames.

    Does not open the transport connection itself: the caller must already
    have awaited ``transport.setup()`` before calling this. Named
    ``start_receiving`` rather than ``connect`` because it starts nothing but
    this engine's own receive thread -- the transport's connection is a
    separate lifecycle the caller owns.

    Raises:
      RuntimeError: If the transport is not yet connected. Starting the
        receive thread against an unconnected transport would otherwise let
        the thread's first ``receive_exact`` raise a ``RuntimeError`` that
        neither ``except TimeoutError`` nor ``except OSError`` in
        :meth:`_rx_loop` catches, so it would fall through to the thread's
        outer handler and die silently instead of surfacing here.
    """
    if self.is_connected:
      return
    if not self._transport.is_connected:
      raise RuntimeError(
        "Transport is not set up; await transport.setup() before engine.start_receiving()."
      )
    self._rx_stop.clear()
    self._rx_thread = threading.Thread(target=self._rx_loop, name="gemini-rx", daemon=True)
    self._rx_thread.start()

  def stop_receiving(self) -> None:
    """Stop the background receive thread.

    Does not close the transport connection; the caller owns that lifecycle
    and may reuse the transport afterward.
    """
    self._rx_stop.set()
    if self._rx_thread is not None:
      self._rx_thread.join(timeout=_RX_STOP_JOIN_S)
      self._rx_thread = None

  def __enter__(self) -> GeminiEngine:
    """Start the receive thread and return this engine."""
    self.start_receiving()
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    """Stop the receive thread."""
    self.stop_receiving()

  # --- Event subscription -------------------------------------------------

  def on_trigger(self, cb: PacketCallback) -> None:
    """Register a callback for incoming ``TRIGGER`` (subcmd=0) packets.

    These are how axes signal event numbers -- move start, move complete, or
    a reserved event such as STOP or E-stop -- to the host.

    Args:
      cb: Called with the trigger packet.
    """
    self._on_trigger_callbacks.append(cb)

  def remove_trigger(self, cb: PacketCallback) -> None:
    """Deregister a previously-registered trigger callback.

    Args:
      cb: The callback to remove; ignored if not currently registered.
    """
    try:
      self._on_trigger_callbacks.remove(cb)
    except ValueError:
      pass

  def wait_for_trigger_event(self, event_value: int, timeout: float) -> bool:
    """Block until a broadcast TRIGGER with the given event value arrives.

    Used by motion code to wait for the controller's move-complete echo of a
    composite ``SEND_EVT``.

    Args:
      event_value: The exact ``cmd_val`` to wait for.
      timeout: Maximum time to wait, in seconds.

    Returns:
      True if the event arrived before the timeout, False otherwise.
    """
    event = threading.Event()

    def _on_evt(pkt: Packet) -> None:
      """Set the wait event when a trigger packet carries the awaited value.

      Args:
        pkt: The received trigger packet.
      """
      if pkt.cmd_val == event_value:
        event.set()

    self.on_trigger(_on_evt)
    try:
      return event.wait(timeout)
    finally:
      self.remove_trigger(_on_evt)

  def on_stream(self, cb: PacketCallback) -> None:
    """Register a callback for STREAM-type packets (unsolicited datalog).

    Args:
      cb: Called with the stream packet.
    """
    self._on_stream_callbacks.append(cb)

  def on_reserved_event(self, cb: ReservedEventCallback) -> None:
    """Register a callback for RESERVED InstructionEvents.

    Fires whenever a TRIGGER broadcast arrives whose value decodes as a
    composite event with event number 127 (E-stop, light-curtain trip,
    fault, and similar safety events).

    Args:
      cb: Called with the decoded reserved event and the packet it arrived in.
    """
    self._on_reserved_event_callbacks.append(cb)

  # --- Core GET / SET -----------------------------------------------------

  def get_value(
    self,
    address: InstructionAddress,
    sub_command: int,
    timeout: float = 5.0,
  ) -> int:
    """Read a subcommand's raw uint32 value.

    Args:
      address: The controller-tree node to query.
      sub_command: The subcommand to read.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The value returned by the controller.

    Raises:
      GeminiTimeoutError: If no response arrives within ``timeout``.
      NAKError: If the controller rejected the request.
    """
    with self._command_lock:
      self._command_complete.clear()
      self._value_buffer = 0
      self._nak_response = 0
      packet = Packet.get_request(dest=address, sub_command=sub_command)
      self._transport.send(pack_packet_frame(packet))
      if not self._command_complete.wait(timeout):
        raise GeminiTimeoutError(
          f"Gemini GET timeout: {address} subcmd={sub_command}",
          timeout=timeout,
        )
      if self._nak_response != 0:
        raise NAKError(
          self._nak_response,
          sub_command=sub_command,
          dest_node=address.node_id,
          dest_dev=address.dev_id,
        )
      return self._value_buffer

  def get_float(
    self,
    address: InstructionAddress,
    sub_command: int,
    timeout: float = 5.0,
  ) -> float:
    """Read a subcommand's value, interpreted as an IEEE 754 float.

    Args:
      address: The controller-tree node to query.
      sub_command: The subcommand to read.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The decoded float value.
    """
    raw = self.get_value(address, sub_command, timeout)
    return unpack_float32(raw)

  def set_uint(
    self,
    address: InstructionAddress,
    sub_command: int,
    value: int,
    timeout: float = 5.0,
  ) -> None:
    """Write a subcommand's raw uint32 value.

    A broadcast send (``address.node_id == NODE_BROADCAST``) does not wait
    for a response: it sleeps :data:`~.enums.BROADCAST_WAIT_MS` milliseconds
    instead, and if the subcommand is ``TRIGGER`` the packet is also
    self-routed into the local receive queue so this engine's own trigger
    callbacks still fire.

    Args:
      address: The controller-tree node to write to.
      sub_command: The subcommand to set.
      value: The 32-bit value to write.
      timeout: Maximum time to wait for the response, in seconds. Ignored
        for a broadcast send.

    Raises:
      GeminiTimeoutError: If no response arrives within ``timeout``.
      NAKError: If the controller rejected the request.
    """
    with self._command_lock:
      packet = Packet.set_request(dest=address, sub_command=sub_command, value=value)
      logger.debug(
        "tx SET: dest=%d.%d sub=%d val=0x%08x",
        address.node_id,
        address.dev_id,
        sub_command,
        value,
      )
      if address.node_id == NODE_BROADCAST:
        self._transport.send(pack_packet_frame(packet))
        if sub_command == CommonSubCommands.TRIGGER:
          with self._local_queue_lock:
            self._local_queue.append(packet)
        time.sleep(BROADCAST_WAIT_MS / 1000.0)
        return

      self._command_complete.clear()
      self._nak_response = 0
      self._transport.send(pack_packet_frame(packet))
      if not self._command_complete.wait(timeout):
        raise GeminiTimeoutError(
          f"Gemini SET timeout: {address} subcmd={sub_command}",
          timeout=timeout,
        )
      if self._nak_response != 0:
        raise NAKError(
          self._nak_response,
          sub_command=sub_command,
          dest_node=address.node_id,
          dest_dev=address.dev_id,
        )

  def set_float(
    self,
    address: InstructionAddress,
    sub_command: int,
    value: float,
    timeout: float = 5.0,
  ) -> None:
    """Write a subcommand's value, packed as an IEEE 754 float on the wire.

    Args:
      address: The controller-tree node to write to.
      sub_command: The subcommand to set.
      value: The float value to write.
      timeout: Maximum time to wait for the response, in seconds.
    """
    self.set_uint(address, sub_command, pack_float32(value), timeout)

  # --- Multipacket --------------------------------------------------------

  def send_multipacket(
    self,
    packets: list[Packet],
    timeout: float = 10.0,
  ) -> None:
    """Send a batch of packets, chunked into multipackets of at most 64 each.

    Each chunk blocks until the controller returns a
    :class:`~.framing.MultipacketResponse`.

    Args:
      packets: The packets to send, in send order.
      timeout: Maximum time to wait for each chunk's response, in seconds.

    Raises:
      GeminiTimeoutError: If a chunk's response does not arrive within ``timeout``.
      MultipacketError: If a chunk fails: one of its packets was NAK'd.
    """
    if not packets:
      return
    with self._command_lock:
      i = 0
      while i < len(packets):
        chunk = packets[i : i + MAX_PACKETS_PER_MULTIPACKET]
        if logger.isEnabledFor(LOG_LEVEL_IO):
          for p in chunk:
            logger.log(
              LOG_LEVEL_IO,
              "tx MP-pkt: dest=%d.%d sub=%d val=0x%08x",
              p.dest.node_id,
              p.dest.dev_id,
              p.sub_command,
              p.cmd_val,
            )
        self._command_complete.clear()
        self._multipacket_success = False
        self._nak_response = 0
        self._multipacket_error_device = 0
        frame = pack_multipacket_frame(chunk)
        if logger.isEnabledFor(LOG_LEVEL_IO):
          logger.log(LOG_LEVEL_IO, "Gemini TX MP frame %d bytes: %s", len(frame), frame.hex())
        self._transport.send(frame)
        if not self._command_complete.wait(timeout):
          raise GeminiTimeoutError(
            f"Gemini multipacket timeout after {len(chunk)} packets",
            timeout=timeout,
          )
        if not self._multipacket_success:
          raise MultipacketError(
            nak_code=self._nak_response,
            error_device_addr=self._multipacket_error_device,
            num_exchanges=len(chunk),
          )
        i += len(chunk)

  # --- Serial device (plate sensor) ---------------------------------------

  def send_serial(self, payload: bytes, timeout: float = 1.0) -> bytes:
    """Send a 9-byte serial-device payload and return the response bytes.

    Used for peripherals the controller forwards serial bytes to, such as
    the plate-presence sensor. Retries within the timeout window until a
    response whose first byte matches the request's first byte arrives.

    Args:
      payload: Exactly 9 bytes to forward to the serial peripheral.
      timeout: Total time to wait for a matching response, in seconds.

    Returns:
      The response bytes.

    Raises:
      ValueError: If ``payload`` is not exactly 9 bytes.
      GeminiTimeoutError: If no matching response arrives within ``timeout``.
    """
    if len(payload) != 9:
      raise ValueError(f"serial payload must be 9 bytes, got {len(payload)}")
    with self._command_lock:
      self._command_complete.clear()
      self._serial_response = None
      self._transport.send(pack_serial_frame(payload))
      deadline = time.monotonic() + timeout
      while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise GeminiTimeoutError("Gemini serial-packet timeout", timeout=timeout)
        if not self._command_complete.wait(remaining):
          raise GeminiTimeoutError("Gemini serial-packet timeout", timeout=timeout)
        resp = self._serial_response
        # Require at least 8 bytes and a first-byte match.
        if resp is not None and len(resp) >= 8 and resp[0] == payload[0]:
          return resp
        # Spurious response; reset and keep waiting for the real one.
        self._command_complete.clear()

  # --- Receive loop -------------------------------------------------------

  def _rx_loop(self) -> None:
    """Continuously read frames off the transport and dispatch them.

    Runs on the background receive thread until :meth:`close` sets the stop
    flag. Polls with a short read timeout so the stop flag is noticed
    promptly rather than only between frames.
    """
    logger.debug("gemini rx thread starting")
    try:
      while not self._rx_stop.is_set():
        # Drain any self-routed packets before reading from the transport.
        self._drain_local_queue()

        try:
          header_bytes = self._transport.receive_exact(FRAME_HEADER_SIZE, timeout=_RX_POLL_S)
        except TimeoutError:
          continue
        except OSError:
          if self._rx_stop.is_set():
            return
          logger.warning("gemini rx: transport error, stopping")
          return

        try:
          header = FrameHeader.from_bytes(header_bytes)
        except ValueError as exc:
          logger.warning("gemini rx: malformed frame header: %s", exc)
          continue

        if not header.is_valid_sync:
          logger.warning(
            "gemini rx: invalid msg_sync=0x%04x -- discarding",
            header.msg_sync,
          )
          continue

        payload = b""
        if header.payload_size > 0:
          try:
            payload = self._transport.receive_exact(header.payload_size, timeout=1.0)
          except TimeoutError:
            logger.warning(
              "gemini rx: payload (%d bytes) timed out",
              header.payload_size,
            )
            continue

        if logger.isEnabledFor(LOG_LEVEL_IO):
          logger.log(
            LOG_LEVEL_IO,
            "Gemini RX frame type=%d %d bytes: %s",
            header.payload_type,
            len(header_bytes) + len(payload),
            (header_bytes + payload).hex(),
          )
        self._dispatch_frame(header, payload)
    except Exception:  # pragma: no cover -- diagnostic
      logger.exception("gemini rx thread crashed")
    finally:
      logger.debug("gemini rx thread exiting")

  def _drain_local_queue(self) -> None:
    """Process every packet self-routed by a broadcast SET, if any."""
    while True:
      with self._local_queue_lock:
        if not self._local_queue:
          return
        pkt = self._local_queue.popleft()
      try:
        self._process_packet(pkt)
      except Exception:  # pragma: no cover -- diagnostic
        logger.exception("error processing self-routed packet")

  def _dispatch_frame(self, header: FrameHeader, payload: bytes) -> None:
    """Decode one received frame's payload and route it by type.

    Args:
      header: The frame's decoded header.
      payload: The frame's raw payload bytes.
    """
    ptype = header.payload_type
    if ptype == TCPMessageType.PACKET:
      try:
        pkt = Packet.from_bytes(payload)
      except ValueError as exc:
        logger.warning("gemini rx: malformed packet: %s", exc)
        return
      self._process_packet(pkt)
    elif ptype == TCPMessageType.MULTIPACKET:
      try:
        resp = MultipacketResponse.from_bytes(payload)
      except ValueError as exc:
        logger.warning("gemini rx: malformed multipacket response: %s", exc)
        return
      self._process_multipacket_response(resp)
    elif ptype == TCPMessageType.SERIAL_DATA:
      self._process_serial_response(payload)
    else:
      logger.debug("gemini rx: unknown payload_type=%d", ptype)

  def _process_packet(self, packet: Packet) -> None:
    """Update shared response state or fan a packet out to callbacks.

    Args:
      packet: The received packet.
    """
    logger.debug(
      "rx pkt: src=%d.%d dest=%d.%d cmd=%d sub=%d val=0x%08x msgid=%d",
      packet.src.node_id,
      packet.src.dev_id,
      packet.dest.node_id,
      packet.dest.dev_id,
      packet.cmd_type,
      packet.sub_command,
      packet.cmd_val,
      packet.msg_id,
    )
    cmd = packet.cmd_type
    if cmd == CommandTypes.SETCMD_RESP:
      self._nak_response = 0
      self._command_complete.set()
    elif cmd == CommandTypes.GETCMD_RESP:
      self._nak_response = 0
      self._value_buffer = packet.cmd_val
      self._command_complete.set()
    elif cmd == CommandTypes.SETCMD_ERR_RESP or cmd == CommandTypes.GETCMD_ERR_RESP:
      self._nak_response = packet.cmd_val & 0xFF
      self._command_complete.set()
    elif cmd == CommandTypes.SETCMD and packet.sub_command == CommonSubCommands.TRIGGER:
      # Incoming, or self-routed, trigger event. First check whether it is a
      # RESERVED safety/fault event.
      reserved = is_reserved_event(packet.cmd_val)
      if reserved is not None:
        logger.warning(
          "Gemini RESERVED event from %d.%d: %s (val=0x%x)",
          packet.src.node_id,
          packet.src.dev_id,
          reserved.name,
          packet.cmd_val,
        )
        # On ERROR/FAULT, also read SUBCMD_ERRCODE from the event's source so
        # the log captures what actually failed. This must run on a separate
        # thread: the rx loop cannot call get_value on itself, since that
        # would deadlock waiting for a response it is itself responsible for
        # dispatching.
        if reserved.name in ("ERROR", "FAULT"):
          src = packet.src

          def _fetch_errcode() -> None:
            """Read and log SUBCMD_ERRCODE from the reserved event's source node.

            Runs on its own thread; see the comment above this closure for why.
            """
            try:
              code = self.get_value(src, GeminiSubCommands.ERRCODE, timeout=1.0)
              category = (code >> 16) & 0xFFFF
              specific = code & 0xFFFF
              logger.warning(
                "  SUBCMD_ERRCODE from %d.%d = 0x%08x (category=%d specific=%d)",
                src.node_id,
                src.dev_id,
                code,
                category,
                specific,
              )
            except Exception as exc:
              logger.debug("  (could not read SUBCMD_ERRCODE: %s)", exc)

          threading.Thread(target=_fetch_errcode, daemon=True).start()
        for reserved_cb in self._on_reserved_event_callbacks:
          try:
            reserved_cb(reserved, packet)
          except Exception:  # pragma: no cover
            logger.exception("reserved-event callback raised")
      # Always also fire the generic trigger callbacks (move-complete echoes, etc.).
      for trigger_cb in self._on_trigger_callbacks:
        try:
          trigger_cb(packet)
        except Exception:  # pragma: no cover -- a callback must not kill the rx thread
          logger.exception("trigger callback raised")
    elif cmd == CommandTypes.STREAM:
      for stream_cb in self._on_stream_callbacks:
        try:
          stream_cb(packet)
        except Exception:  # pragma: no cover
          logger.exception("stream callback raised")
    # Other cmd_types (e.g. an inbound GETCMD) are ignored.

  def _process_multipacket_response(self, resp: MultipacketResponse) -> None:
    """Update shared response state from a multipacket response.

    Args:
      resp: The received multipacket response.
    """
    self._multipacket_success = resp.is_success
    if not resp.is_success:
      self._nak_response = resp.device_error_nak
      self._multipacket_error_device = resp.error_device_addr
    else:
      self._nak_response = 0
    self._command_complete.set()

  def _process_serial_response(self, payload: bytes) -> None:
    """Update shared response state from a serial-peripheral response.

    Args:
      payload: The received serial-peripheral payload.
    """
    self._serial_response = payload
    self._command_complete.set()

  # --- Master-node convenience helpers ------------------------------------

  def master_get_uint(self, sub_command: int, timeout: float = 5.0) -> int:
    """Read a subcommand's raw uint32 value from the master node.

    Args:
      sub_command: The subcommand to read.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The value returned by the controller.
    """
    return self.get_value(MASTER_ADDRESS, sub_command, timeout)

  def master_set_uint(self, sub_command: int, value: int, timeout: float = 5.0) -> None:
    """Write a subcommand's raw uint32 value on the master node.

    Args:
      sub_command: The subcommand to set.
      value: The 32-bit value to write.
      timeout: Maximum time to wait for the response, in seconds.
    """
    self.set_uint(MASTER_ADDRESS, sub_command, value, timeout)

  def master_get_float(self, sub_command: int, timeout: float = 5.0) -> float:
    """Read a subcommand's value from the master node, as an IEEE 754 float.

    Args:
      sub_command: The subcommand to read.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The decoded float value.
    """
    return unpack_float32(self.master_get_uint(sub_command, timeout))

  def master_set_float(self, sub_command: int, value: float, timeout: float = 5.0) -> None:
    """Write a subcommand's value on the master node, packed as a float.

    Args:
      sub_command: The subcommand to set.
      value: The float value to write.
      timeout: Maximum time to wait for the response, in seconds.
    """
    self.master_set_uint(sub_command, pack_float32(value), timeout)
