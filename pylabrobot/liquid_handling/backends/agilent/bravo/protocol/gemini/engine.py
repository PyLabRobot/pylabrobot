"""GeminiEngine — request/response dispatcher over a framed TCP socket.

Serializes every command under a single lock with one shared
"response-complete" Event, so there's only ever one request outstanding. This
port preserves that shape — simple, correct, and matches observed wire
behavior byte-for-byte.

Usage::

    engine = GeminiEngine("192.168.0.8")
    engine.connect()
    try:
        fw = engine.get_value(InstructionAddress(4), CommonSubCommands.FW_VERSION)
    finally:
        engine.close()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
    BROADCAST_WAIT_MS,
    FRAME_HEADER_SIZE,
    MAX_PACKETS_PER_MULTIPACKET,
    NODE_BROADCAST,
    TCP_PORT,
    CommandNAKTypes,
    CommandTypes,
    CommonSubCommands,
    ReservedEvent,
    SubCommandDataType,
    TCPMessageType,
    is_reserved_event,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.errors import (
    GeminiProtocolError,
    GeminiTimeoutError,
    MultipacketError,
    NAKError,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.framing import (
    FrameHeader,
    MultipacketResponse,
    pack_multipacket_frame,
    pack_packet_frame,
    pack_serial_frame,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.instruction import pack_float32, unpack_float32
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import (
    HOST_ADDRESS,
    InstructionAddress,
    Packet,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.logging_config import TRACE
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.tcp import TCPTransport

logger = logging.getLogger(__name__)


# Short blocking read for the rx thread — lets us poll the stop flag.
_RX_POLL_MS = 100
# How long we give the rx thread to drain after close() is called.
_RX_STOP_JOIN_S = 2.0


PacketCallback = Callable[[Packet], None]
ReservedEventCallback = Callable[[ReservedEvent, Packet], None]


class GeminiEngine:
    """Synchronous get/set/multipacket dispatcher with a background rx thread.

    Threading model:
    - ``_command_lock`` serializes every command-issuing method so only one
      request is in flight at a time.
    - ``_command_complete`` is a single Event reset before each send and set by
      the rx thread when any response matching-shape arrives.
    - Shared state (``_value_buffer``, ``_nak_response``, etc.) is populated by
      the rx thread and read by the caller under the command lock.
    - msgID in the packet is not used for correlation — ``_command_lock``
      guarantees there's only one outstanding request.
    """

    def __init__(
        self,
        address: str,
        port: int = TCP_PORT,
        *,
        connect_timeout: float = 5.0,
    ):
        self._transport = TCPTransport(address, port, connect_timeout)
        self._command_lock = threading.Lock()
        self._command_complete = threading.Event()

        # Response state (written by rx thread, read under command lock)
        self._value_buffer: int = 0
        self._nak_response: int = 0
        self._multipacket_success: bool = False
        self._multipacket_error_device: int = 0
        self._serial_response: bytes | None = None

        self._rx_stop = threading.Event()
        self._rx_thread: threading.Thread | None = None

        # Self-routed broadcast trigger queue (mirrors MasterNode.localPacketQueue).
        self._local_queue: deque[Packet] = deque()
        self._local_queue_lock = threading.Lock()

        # Callbacks for unsolicited / stream packets
        self._on_trigger_callbacks: list[PacketCallback] = []
        self._on_stream_callbacks: list[PacketCallback] = []
        self._on_reserved_event_callbacks: list[ReservedEventCallback] = []

    # --- Lifecycle ----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected and self._rx_thread is not None \
            and self._rx_thread.is_alive()

    def connect(self) -> None:
        if self.is_connected:
            return
        self._transport.connect()
        self._rx_stop.clear()
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name="gemini-rx", daemon=True
        )
        self._rx_thread.start()

    def close(self) -> None:
        self._rx_stop.set()
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=_RX_STOP_JOIN_S)
            self._rx_thread = None
        self._transport.disconnect()

    def __enter__(self) -> "GeminiEngine":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- Event subscription -------------------------------------------------

    def on_trigger(self, cb: PacketCallback) -> None:
        """Register a callback for incoming SUBCMD_TRIGGER (subcmd=0) packets.

        These are how axes signal event numbers (move start / move complete /
        reserved events like STOP / E-STOP) to the host.
        """
        self._on_trigger_callbacks.append(cb)

    def remove_trigger(self, cb: PacketCallback) -> None:
        """Deregister a previously-registered trigger callback."""
        try:
            self._on_trigger_callbacks.remove(cb)
        except ValueError:
            pass

    def wait_for_trigger_event(
        self, event_value: int, timeout_ms: int
    ) -> bool:
        """Block until a broadcast SUBCMD_TRIGGER with the given event value arrives.

        Returns True on match, False on timeout. Used by motion code to wait
        for the controller's move-complete echo of a composite SEND_EVT.
        """
        event = threading.Event()

        def _on_evt(pkt: Packet) -> None:
            if pkt.cmd_val == event_value:
                event.set()

        self.on_trigger(_on_evt)
        try:
            return event.wait(timeout_ms / 1000.0)
        finally:
            self.remove_trigger(_on_evt)

    def on_stream(self, cb: PacketCallback) -> None:
        """Register a callback for STREAM-type packets (unsolicited datalog)."""
        self._on_stream_callbacks.append(cb)

    def on_reserved_event(self, cb: ReservedEventCallback) -> None:
        """Register a callback for RESERVED InstructionEvents (E-stop, light
        curtain, fault, etc.) broadcast by the controller.

        Callback signature: ``cb(reserved: ReservedEvent, packet: Packet)``.
        Fires whenever a TRIGGER (subcmd=0) broadcast arrives where the value
        decodes as a composite event with event_no=127.
        """
        self._on_reserved_event_callbacks.append(cb)

    # --- Core GET / SET -----------------------------------------------------

    def get_value(
        self,
        address: InstructionAddress,
        sub_command: int,
        timeout_ms: int = 5000,
    ) -> int:
        """Synchronous GET → returns the uint32 response value."""
        with self._command_lock:
            self._command_complete.clear()
            self._value_buffer = 0
            self._nak_response = 0
            packet = Packet.get_request(dest=address, sub_command=sub_command)
            self._transport.send(pack_packet_frame(packet))
            if not self._command_complete.wait(timeout_ms / 1000.0):
                raise GeminiTimeoutError(
                    f"Gemini GET timeout: {address} subcmd={sub_command}",
                    timeout_ms=timeout_ms,
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
        timeout_ms: int = 5000,
    ) -> float:
        """GET a value, interpret as IEEE 754 single-precision float."""
        raw = self.get_value(address, sub_command, timeout_ms)
        return unpack_float32(raw)

    def set_uint(
        self,
        address: InstructionAddress,
        sub_command: int,
        value: int,
        timeout_ms: int = 5000,
    ) -> None:
        """Synchronous SET of a uint32 value.

        Broadcast sends (``address.node_id == 63``) don't wait for a response —
        they sleep ``BROADCAST_WAIT_MS`` milliseconds. 
        If the subcommand is ``TRIGGER`` the packet is also
        self-routed into the local receive queue so event callbacks fire.
        """
        with self._command_lock:
            packet = Packet.set_request(
                dest=address, sub_command=sub_command, value=value
            )
            logger.debug(
                "tx SET: dest=%d.%d sub=%d val=0x%08x",
                address.node_id, address.dev_id, sub_command, value,
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
            if not self._command_complete.wait(timeout_ms / 1000.0):
                raise GeminiTimeoutError(
                    f"Gemini SET timeout: {address} subcmd={sub_command}",
                    timeout_ms=timeout_ms,
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
        timeout_ms: int = 5000,
    ) -> None:
        """SET a value given a float; packed as float32 on the wire."""
        self.set_uint(address, sub_command, pack_float32(value), timeout_ms)

    # --- Multipacket --------------------------------------------------------

    def send_multipacket(
        self,
        packets: list[Packet],
        timeout_ms: int = 10_000,
    ) -> None:
        """Send a batch of packets, chunked into multipackets of ≤64 each.

        Each chunk blocks until the controller returns a ``MultipacketResponse``.
        Raises :class:`MultipacketError` on failure of any chunk.
        """
        if not packets:
            return
        with self._command_lock:
            i = 0
            while i < len(packets):
                chunk = packets[i:i + MAX_PACKETS_PER_MULTIPACKET]
                if logger.isEnabledFor(logging.DEBUG):
                    for p in chunk:
                        logger.debug(
                            "tx MP-pkt: dest=%d.%d sub=%d val=0x%08x",
                            p.dest.node_id, p.dest.dev_id,
                            p.sub_command, p.cmd_val,
                        )
                self._command_complete.clear()
                self._multipacket_success = False
                self._nak_response = 0
                self._multipacket_error_device = 0
                frame = pack_multipacket_frame(chunk)
                if logger.isEnabledFor(TRACE):
                    logger.log(TRACE, "Gemini TX MP frame %d bytes: %s", len(frame), frame.hex())
                self._transport.send(frame)
                if not self._command_complete.wait(timeout_ms / 1000.0):
                    raise GeminiTimeoutError(
                        f"Gemini multipacket timeout after {len(chunk)} packets",
                        timeout_ms=timeout_ms,
                    )
                if not self._multipacket_success:
                    raise MultipacketError(
                        nak_code=self._nak_response,
                        error_device_addr=self._multipacket_error_device,
                        num_exchanges=len(chunk),
                    )
                i += len(chunk)

    # --- Serial device (plate sensor) ---------------------------------------

    def send_serial(self, payload: bytes, timeout_ms: int = 1000) -> bytes:
        """Send a 9-byte serial-device payload and return the response bytes.

        Used for peripherals the controller forwards serial bytes to (e.g. the
        plate-presence sensor). Retries within the timeout window until the
        response's first byte matches the request's first byte.
        """
        if len(payload) != 9:
            raise ValueError(f"serial payload must be 9 bytes, got {len(payload)}")
        with self._command_lock:
            self._command_complete.clear()
            self._serial_response = None
            self._transport.send(pack_serial_frame(payload))
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GeminiTimeoutError(
                        "Gemini serial-packet timeout", timeout_ms=timeout_ms
                    )
                if not self._command_complete.wait(remaining):
                    raise GeminiTimeoutError(
                        "Gemini serial-packet timeout", timeout_ms=timeout_ms
                    )
                resp = self._serial_response
                # Require at least 8 bytes AND first byte match (mirrors C#).
                if resp is not None and len(resp) >= 8 and resp[0] == payload[0]:
                    return resp
                # Spurious; reset and keep waiting.
                self._command_complete.clear()

    # --- Receive loop -------------------------------------------------------

    def _rx_loop(self) -> None:
        logger.debug("gemini rx thread starting")
        try:
            while not self._rx_stop.is_set():
                # Drain any self-routed packets before reading from the socket.
                self._drain_local_queue()

                try:
                    header_bytes = self._transport.receive_exact(
                        FRAME_HEADER_SIZE, timeout_ms=_RX_POLL_MS
                    )
                except TimeoutError:
                    continue
                except (ConnectionError, OSError):
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
                        "gemini rx: invalid msg_sync=0x%04x — discarding",
                        header.msg_sync,
                    )
                    continue

                payload = b""
                if header.payload_size > 0:
                    try:
                        payload = self._transport.receive_exact(
                            header.payload_size, timeout_ms=1000
                        )
                    except TimeoutError:
                        logger.warning(
                            "gemini rx: payload (%d bytes) timed out",
                            header.payload_size,
                        )
                        continue

                if logger.isEnabledFor(TRACE):
                    logger.log(
                        TRACE, "Gemini RX frame type=%d %d bytes: %s",
                        header.msg_type, len(header_bytes) + len(payload),
                        (header_bytes + payload).hex(),
                    )
                self._dispatch_frame(header, payload)
        except Exception:  # pragma: no cover — diagnostic
            logger.exception("gemini rx thread crashed")
        finally:
            logger.debug("gemini rx thread exiting")

    def _drain_local_queue(self) -> None:
        while True:
            with self._local_queue_lock:
                if not self._local_queue:
                    return
                pkt = self._local_queue.popleft()
            try:
                self._process_packet(pkt)
            except Exception:  # pragma: no cover — diagnostic
                logger.exception("error processing self-routed packet")

    def _dispatch_frame(self, header: FrameHeader, payload: bytes) -> None:
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
        logger.debug(
            "rx pkt: src=%d.%d dest=%d.%d cmd=%d sub=%d val=0x%08x msgid=%d",
            packet.src.node_id, packet.src.dev_id,
            packet.dest.node_id, packet.dest.dev_id,
            packet.cmd_type, packet.sub_command, packet.cmd_val, packet.msg_id,
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
            # Incoming / self-routed trigger event.
            # First, check if it's a RESERVED safety/fault event.
            reserved = is_reserved_event(packet.cmd_val)
            if reserved is not None:
                logger.warning(
                    "Gemini RESERVED event from %d.%d: %s (val=0x%x)",
                    packet.src.node_id, packet.src.dev_id,
                    reserved.name, packet.cmd_val,
                )
                # On ERROR/FAULT the client also issues a follow-up
                # GET for SUBCMD_ERRCODE on the source address to obtain the
                # actual firmware error code. We do the same so every server
                # log captures "what actually failed" — crucial for diagnosing
                # retract failures after force moves, etc. Must run on a
                # separate thread: the rx loop can't call get_value on itself
                # (it'd deadlock waiting for a response it's supposed to
                # dispatch).
                if reserved.name in ("ERROR", "FAULT"):
                    src = packet.src
                    def _fetch_errcode() -> None:
                        try:
                            code = self.get_value(
                                src,
                                GeminiSubCommands.ERRCODE,
                                timeout_ms=1000,
                            )
                            category = (code >> 16) & 0xFFFF
                            specific = code & 0xFFFF
                            logger.warning(
                                "  SUBCMD_ERRCODE from %d.%d = "
                                "0x%08x (category=%d specific=%d)",
                                src.node_id, src.dev_id, code,
                                category, specific,
                            )
                        except Exception as exc:
                            logger.debug(
                                "  (could not read SUBCMD_ERRCODE: %s)", exc
                            )
                    threading.Thread(target=_fetch_errcode, daemon=True).start()
                for cb in self._on_reserved_event_callbacks:
                    try:
                        cb(reserved, packet)
                    except Exception:  # pragma: no cover
                        logger.exception("reserved-event callback raised")
            # Always also fire the generic trigger callbacks (move-complete echoes etc.)
            for cb in self._on_trigger_callbacks:
                try:
                    cb(packet)
                except Exception:  # pragma: no cover — callback shouldn't kill rx
                    logger.exception("trigger callback raised")
        elif cmd == CommandTypes.STREAM:
            for cb in self._on_stream_callbacks:
                try:
                    cb(packet)
                except Exception:  # pragma: no cover
                    logger.exception("stream callback raised")
        # Other cmd_types (e.g. inbound GETCMD) are ignored.

    def _process_multipacket_response(self, resp: MultipacketResponse) -> None:
        self._multipacket_success = resp.is_success
        if not resp.is_success:
            self._nak_response = resp.device_error_nak
            self._multipacket_error_device = resp.error_device_addr
        else:
            self._nak_response = 0
        self._command_complete.set()

    def _process_serial_response(self, payload: bytes) -> None:
        self._serial_response = payload
        self._command_complete.set()

    # --- Master-node convenience helpers ------------------------------------

    def master_get_uint(
        self, sub_command: int, timeout_ms: int = 5000
    ) -> int:
        from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import MASTER_ADDRESS
        return self.get_value(MASTER_ADDRESS, sub_command, timeout_ms)

    def master_set_uint(
        self, sub_command: int, value: int, timeout_ms: int = 5000
    ) -> None:
        from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import MASTER_ADDRESS
        self.set_uint(MASTER_ADDRESS, sub_command, value, timeout_ms)

    def master_get_float(
        self, sub_command: int, timeout_ms: int = 5000
    ) -> float:
        return unpack_float32(self.master_get_uint(sub_command, timeout_ms))

    def master_set_float(
        self, sub_command: int, value: float, timeout_ms: int = 5000
    ) -> None:
        self.master_set_uint(sub_command, pack_float32(value), timeout_ms)
