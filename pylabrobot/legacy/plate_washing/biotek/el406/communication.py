"""EL406 low-level communication methods.

This module contains the mixin class for low-level USB/FTDI communication
with the BioTek EL406 plate washer.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, NamedTuple

import anyio

from pylabrobot.io.binary import Reader

from .error_codes import get_error_message
from .errors import EL406CommunicationError, EL406DeviceError
from .protocol import build_framed_message

LONG_READ_TIMEOUT = 120.0  # seconds, for long operations (wash cycles can take >30s)

STATE_INITIAL = 1
STATE_RUNNING = 2
STATE_PAUSED = 3
STATE_STOPPED = 4

if TYPE_CHECKING:
  from pylabrobot.io.ftdi import FTDI


class DevicePollResult(NamedTuple):
  """Parsed result from a STATUS_POLL response."""

  validity: int
  state: int
  status: int
  raw_response: bytes


logger = logging.getLogger(__name__)


class EL406CommunicationMixin:
  """Mixin providing low-level communication methods for the EL406.

  This mixin provides:
  - Buffer purging
  - Framed command sending
  - Action command sending (with completion wait)
  - Framed query sending
  - Low-level byte reading

  Requires:
    self.io: FTDI IO wrapper instance
    self.timeout: Default timeout in seconds
    self._command_lock: anyio.Lock for command serialization
  """

  io: FTDI | None
  timeout: float
  _command_lock: anyio.Lock | None

  async def _write_to_device(self, data: bytes) -> None:
    """Write bytes to the FTDI device, wrapping errors.

    Raises:
      EL406CommunicationError: If the write fails.
    """
    assert self.io is not None
    try:
      await self.io.write(data)
    except Exception as e:
      raise EL406CommunicationError(
        f"Failed to write to device: {e}. Device may have disconnected.",
        operation="write",
        original_error=e,
      ) from e

  async def _wait_for_ack(self) -> None:
    """Wait for an ACK while the caller owns the response deadline."""
    assert self.io is not None
    while True:
      byte = await self.io.read(1)
      if byte[0] == 0x15:  # NAK
        raise RuntimeError(
          f"Device rejected command (NAK). Response: {byte!r}. "
          "This may indicate an invalid command, bad parameters, or device busy state."
        )
      if byte[0] == 0x06:  # ACK
        return

  async def _read_exact_bytes(self, count: int, buf: bytearray) -> None:
    """Append bytes to a reply buffer within the caller's deadline scope."""
    assert self.io is not None
    while len(buf) < count:
      buf.extend(await self.io.read(count - len(buf)))

  async def _purge_buffers(self) -> None:
    """Purge the RX and TX buffers."""
    if self.io is None:
      return

    try:
      for _ in range(6):
        await self.io.usb_purge_rx_buffer()
      await self.io.usb_purge_tx_buffer()
    except Exception as e:
      raise EL406CommunicationError(
        f"Failed to purge FTDI buffers: {e}. Device may have disconnected.",
        operation="purge",
        original_error=e,
      ) from e

  async def _test_communication(self) -> None:
    """Test communication with the device.

    Sends framed command 0x73 (115) and expects ACK (0x06) response.

    Raises:
      RuntimeError: If communication test fails.
    """
    if self.io is None:
      raise RuntimeError("EL406 communication test failed: device not open")

    try:
      framed_command = build_framed_message(command=0x73)
      response = await self._send_framed_command(framed_command, timeout=self.timeout)
      if 0x06 not in response:
        raise RuntimeError(
          f"EL406 communication test failed: expected ACK (0x06), got {response!r}"
        )
    except TimeoutError as e:
      raise RuntimeError(f"EL406 communication test failed: timeout - {e}") from e

    logger.info("EL406 communication test passed")

    # Send INIT_STATE (0xA0) command to clear device state
    logger.info("Sending INIT_STATE command (0xA0) to clear device state")
    init_state_cmd = build_framed_message(command=0xA0)
    init_response = await self._send_framed_command(init_state_cmd, timeout=self.timeout)
    logger.debug("INIT_STATE sent, response: %s", init_response.hex())

  async def start_batch(self, wire_byte: int) -> None:
    """Send START_STEP command to begin a batch of step operations.

    Use this function at the beginning of a protocol, before executing any step
    commands. This puts the device in "ready to execute steps" mode. Must be
    called once before running step commands like prime, dispense, aspirate,
    shake, etc.

    This should be called:
    - After setup() completes
    - Before running any step commands
    - Only once per batch of operations (not before each individual step)

    Args:
      wire_byte: EL406 plate-type byte for the wire protocol.
    """
    await self._start_batch(wire_byte)

  async def _start_batch(self, wire_byte: int, on_commit: Callable[[], None] | None = None) -> None:
    """Start a batch and report when the START_STEP header is committed."""
    if self.io is None:
      raise RuntimeError("Device not initialized - call setup() first")

    logger.info("Sending START_STEP to begin batch operations")

    # Send initialization commands before START_STEP
    pre_batch_commands = [0xBF, 0xC1, 0xF2, 0xF4, 0x0154, 0x0102, 0x010A]
    for cmd in pre_batch_commands:
      cmd_frame = build_framed_message(cmd)
      try:
        resp = await self._send_framed_command(cmd_frame, timeout=self.timeout)
        logger.debug("Command 0x%04X response: %s", cmd, resp.hex())
      except Exception as e:
        logger.warning("Pre-batch command 0x%04X failed: %s", cmd, e)

    # Data byte is the plate type value (e.g., 0x04 for 96-well, 0x01 for 384-well).
    start_step_data = bytes([wire_byte])
    start_step_cmd = build_framed_message(command=0x8D, data=start_step_data)
    response = await self._send_framed_command(
      start_step_cmd, timeout=self.timeout, _on_commit=on_commit
    )
    logger.debug("START_STEP sent, response: %s", response.hex())

  async def _send_framed_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
    *,
    _on_commit: Callable[[], None] | None = None,
  ) -> bytes:
    """Send a framed command and wait for full response.

    The device responds to framed commands with:
    - ACK (0x06) + 11-byte header + N-byte data

    This method reads the complete response to avoid leaving data in the buffer.
    For ACK-only commands (e.g. TEST_COMM, INIT_STATE), the header wait acts as
    an implicit settling delay that the device needs before accepting further
    commands.

    Args:
      framed_message: Complete framed message (from build_framed_message).
      timeout: Timeout in seconds.

    Returns:
      Complete response bytes (ACK + header + data).

    Raises:
      TimeoutError: If timeout waiting for response.
    """
    if self.io is None or self._command_lock is None:
      raise RuntimeError("Device not initialized")

    if timeout is None:
      timeout = self.timeout

    async def exchange() -> bytes:
      if _on_commit is None:
        return await self._exchange_framed_command(framed_message, timeout)
      return await self._exchange_framed_command(framed_message, timeout, _on_commit=_on_commit)

    return await self._run_locked_transaction(exchange)

  async def _run_locked_transaction(
    self,
    exchange: Callable[[], Awaitable[bytes]],
  ) -> bytes:
    """Run committed protocol work shielded while retaining the command lock."""
    assert self._command_lock is not None
    lock = self._command_lock
    await lock.acquire()
    try:
      await self._purge_buffers()
      await anyio.lowlevel.checkpoint()
      error: Exception | None = None
      result: bytes | None = None
      with anyio.CancelScope(shield=True):
        try:
          result = await exchange()
        except Exception as exc:
          error = exc
    finally:
      lock.release()

    # Deliver cancellation only after reconciliation and lock release. If there
    # is none pending, preserve the committed operation's original exception.
    await anyio.lowlevel.checkpoint()
    if error is not None:
      raise error
    assert result is not None
    return result

  async def _exchange_framed_command(
    self,
    framed_message: bytes,
    timeout: float,
    *,
    _on_commit: Callable[[], None] | None = None,
  ) -> bytes:
    """Exchange one framed command while the caller owns the command lock."""
    header = framed_message[:11]
    data = framed_message[11:] if len(framed_message) > 11 else b""

    with anyio.move_on_after(timeout) as write_scope:
      await self._write_to_device(header)
      if _on_commit is not None:
        _on_commit()
      logger.debug("Sent header: %s", header.hex())

      if data:
        await anyio.sleep(0.001)
        await self._write_to_device(data)
        logger.debug("Sent data: %s", data.hex())
    if write_scope.cancel_called:
      raise TimeoutError("Timeout writing framed command")
    logger.debug("Sent framed: %s", framed_message.hex())

    resp_header = bytearray()
    payload = bytearray()
    ack_received = False
    with anyio.move_on_after(timeout):
      await self._wait_for_ack()
      ack_received = True
      await self._read_exact_bytes(11, resp_header)
      data_len = Reader(resp_header[7:]).u16()
      await self._read_exact_bytes(data_len, payload)
    if not ack_received:
      raise TimeoutError("Timeout waiting for ACK")
    result = b"\x06" + bytes(resp_header) + bytes(payload)
    if len(resp_header) == 11:
      logger.debug("Full response: %s (%d bytes)", result.hex(), len(result))
    else:
      logger.debug("ACK-only response (no frame): %s", result.hex())

    return result

  async def _send_action_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
  ) -> bytes:
    """Send an action command and wait for completion frame.

    Action commands (like reset, home_motors) work differently from query commands:
    1. Send command
    2. Device sends ACK immediately (acknowledging receipt)
    3. Device performs the physical action (takes time)
    4. Device sends completion frame when done

    This method waits for both the ACK and the completion frame.

    Args:
      framed_message: Complete framed message (from build_framed_message).
      timeout: Timeout in seconds for the entire operation including action completion.

    Returns:
      Completion frame bytes (header + data).

    Raises:
      TimeoutError: If timeout waiting for ACK or completion.
      RuntimeError: If device rejects command (NAK).
    """
    if self.io is None or self._command_lock is None:
      raise RuntimeError("Device not initialized")

    if timeout is None:
      timeout = LONG_READ_TIMEOUT  # Default to long timeout for actions

    async def exchange() -> bytes:
      return await self._exchange_action_command(framed_message, timeout)

    return await self._run_locked_transaction(exchange)

  async def _exchange_action_command(self, framed_message: bytes, timeout: float) -> bytes:
    """Exchange one action command while the caller owns the command lock."""
    header = framed_message[:11]
    data = framed_message[11:] if len(framed_message) > 11 else b""

    with anyio.move_on_after(timeout) as write_scope:
      await self._write_to_device(header)
      if data:
        await anyio.sleep(0.001)
        await self._write_to_device(data)
    if write_scope.cancel_called:
      raise TimeoutError("Timeout writing action command")
    logger.debug("Sent action command: %s", framed_message.hex())

    header = bytearray()
    data = bytearray()
    ack_received = False
    with anyio.move_on_after(timeout):
      with anyio.fail_after(min(timeout, self.timeout)):
        await self._wait_for_ack()
      ack_received = True
      logger.debug("Got ACK, waiting for completion...")
      await self._read_exact_bytes(11, header)
      data_len = Reader(header[7:]).u16()
      await self._read_exact_bytes(data_len, data)
    if not ack_received:
      raise TimeoutError("Timeout waiting for ACK")
    if len(header) < 11:
      raise TimeoutError(f"Timeout waiting for completion header (got {len(header)} bytes)")

    data_len = Reader(header[7:]).u16()
    result = bytes(header + data)

    logger.debug("Completion frame: %s (%d bytes)", result.hex(), len(result))

    cmd_echo = Reader(result[2:]).u16()
    response_data = result[11 : 11 + data_len] if len(result) >= 11 + data_len else b""
    logger.debug("  Command echo: 0x%04X, data: %s", cmd_echo, response_data.hex())

    return result

  async def _send_framed_query(
    self,
    command: int,
    data: bytes = b"",
    timeout: float | None = None,
  ) -> bytes:
    """Send a framed query command and read full response with header and data.

    Sends the 11-byte header and optional data payload as separate USB writes,
    then reads the full response: ACK + 11-byte response header + data.

    Args:
      command: 16-bit command code
      data: Optional data bytes to send with command
      timeout: Timeout in seconds

    Returns:
      Data bytes from response (header stripped).

    Raises:
      RuntimeError: If device not initialized or response invalid.
      TimeoutError: If timeout waiting for response.
    """
    if self.io is None or self._command_lock is None:
      raise RuntimeError("Device not initialized")

    if timeout is None:
      timeout = self.timeout

    framed_message = build_framed_message(command, data)

    async def exchange() -> bytes:
      return await self._exchange_framed_query(command, framed_message, timeout)

    return await self._run_locked_transaction(exchange)

  async def _exchange_framed_query(
    self, command: int, framed_message: bytes, timeout: float
  ) -> bytes:
    """Exchange one framed query while the caller owns the command lock."""
    msg_header = framed_message[:11]
    msg_data = framed_message[11:] if len(framed_message) > 11 else b""

    with anyio.move_on_after(timeout) as write_scope:
      await self._write_to_device(msg_header)
      logger.debug("Sent query header 0x%04X: %s", command, msg_header.hex())

      if msg_data:
        await anyio.sleep(0.001)
        await self._write_to_device(msg_data)
        logger.debug("Sent query data: %s", msg_data.hex())
    if write_scope.cancel_called:
      raise TimeoutError(f"Timeout writing command 0x{command:04X}")

    resp_header = bytearray()
    response_data = bytearray()
    ack_received = False
    try:
      with anyio.move_on_after(timeout):
        await self._wait_for_ack()
        ack_received = True
        await self._read_exact_bytes(11, resp_header)
        data_len = Reader(resp_header[7:]).u16()
        await self._read_exact_bytes(data_len, response_data)
    except RuntimeError as e:
      raise RuntimeError(
        f"Device rejected command 0x{command:04X} (NAK). Check command code and parameters."
      ) from e
    except TimeoutError as e:
      raise TimeoutError(f"Timeout waiting for ACK (command 0x{command:04X})") from e

    if not ack_received:
      raise TimeoutError(f"Timeout waiting for ACK (command 0x{command:04X})")
    if len(resp_header) < 11:
      raise TimeoutError(f"Timeout reading response header (got {len(resp_header)}/11 bytes)")

    logger.debug("Response header: %s", resp_header.hex())

    data_len = Reader(resp_header[7:]).u16()
    logger.debug("Response data length: %d", data_len)

    if len(response_data) < data_len:
      raise TimeoutError(
        f"Timeout reading response data (got {len(response_data)}/{data_len} bytes)"
      )

    logger.debug("Response data: %s", response_data.hex())
    return bytes(response_data)

  async def _poll_device_state(self) -> DevicePollResult:
    """Send one STATUS_POLL and return the parsed device state.

    Returns:
      DevicePollResult with validity, state, status, and raw_response.

    Raises:
      EL406CommunicationError: If poll response is too short to parse.
    """
    poll_command = build_framed_message(command=0x92)
    poll_response = await self._send_framed_command(poll_command, timeout=self.timeout)
    return self._parse_device_poll_response(poll_response)

  def _parse_device_poll_response(self, poll_response: bytes) -> DevicePollResult:
    """Parse one STATUS_POLL response already read from the device."""
    logger.debug("Status poll response (%d bytes): %s", len(poll_response), poll_response.hex())

    if len(poll_response) < 21:
      # Short response — return zeroed fields so callers can handle it
      return DevicePollResult(validity=0, state=0, status=0, raw_response=poll_response)

    # Data layout (after ACK+header at offset 12):
    #   bytes 12-13: validity (little-endian, must be 0)
    #   bytes 14-15: state (little-endian)
    #   bytes 16-19: timestamp/counter
    #   byte 20:     status code
    r = Reader(poll_response[12:])
    validity = r.u16()
    state = r.u16()
    r.raw_bytes(4)  # skip timestamp/counter (bytes 16-19)
    status = r.u8()

    if validity != 0:
      error_msg = get_error_message(validity)
      logger.warning("Status poll returned error 0x%04X (%d): %s", validity, validity, error_msg)

    logger.debug("Status poll: validity=%d, state=%d, status=%d", validity, state, status)
    return DevicePollResult(
      validity=validity, state=state, status=status, raw_response=poll_response
    )

  async def _wait_until_ready(self, timeout: float = 5.0, poll_interval: float = 0.1) -> None:
    """Poll until the device is no longer in STATE_RUNNING.

    Args:
      timeout: Maximum time to wait in seconds.
      poll_interval: Time between polls in seconds.

    Raises:
      TimeoutError: If the device stays busy beyond *timeout*.
    """
    with anyio.move_on_after(timeout) as scope:
      while True:
        poll = await self._poll_device_state()
        if poll.state != STATE_RUNNING:
          return
        await anyio.sleep(poll_interval)
    assert scope.cancel_called
    raise TimeoutError(f"Device still busy (STATE_RUNNING) after {timeout}s waiting for readiness")

  async def _send_step_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
    poll_interval: float = 0.1,
  ) -> bytes:
    """Send a step command and poll for completion.

    Step commands (prime, dispense, aspirate, shake, etc.) require polling
    for completion using STATUS_POLL (0x92) until the operation completes.

    Protocol flow:
    1. Wait for device to be ready (not RUNNING)
    2. Send step command (e.g., SYRINGE_PRIME 0xA2)
    3. Device ACKs immediately
    4. Poll with STATUS_POLL (0x92) repeatedly
    5. Check state in response to determine completion

    Args:
      framed_message: Complete framed message (from build_framed_message).
      timeout: Timeout in seconds for the entire operation.
      poll_interval: Time between status polls in seconds.

    Returns:
      Final status response bytes.

    Raises:
      TimeoutError: If timeout waiting for completion.
      EL406DeviceError: If device reports an error during the step.
      RuntimeError: If device rejects command (NAK).
    """
    if self.io is None or self._command_lock is None:
      raise RuntimeError("Device not initialized")

    if timeout is None:
      timeout = LONG_READ_TIMEOUT

    logger.debug("Starting step command with timeout=%ss", timeout)

    # 1. Wait for device to be ready (not RUNNING)
    await self._wait_until_ready(timeout=min(timeout, self.timeout))

    async def exchange_step() -> bytes:
      logger.debug("Sending step command: %s", framed_message.hex())
      response = await self._exchange_framed_command(
        framed_message, timeout=min(timeout, self.timeout)
      )
      logger.debug("Step command sent, got initial response: %s", response.hex())

      await anyio.sleep(0.5)
      poll_count = 0
      logger.debug("Starting polling loop...")

      with anyio.move_on_after(timeout) as scope:
        while True:
          await anyio.sleep(poll_interval)
          poll_count += 1

          await self._purge_buffers()
          poll_response = await self._exchange_framed_command(
            build_framed_message(command=0x92), timeout=self.timeout
          )
          poll = self._parse_device_poll_response(poll_response)
          logger.debug("Poll #%d: %d bytes", poll_count, len(poll.raw_response))

          if poll.state in (STATE_INITIAL, STATE_STOPPED):
            logger.debug("Step completed (state=%d) after %d polls", poll.state, poll_count)
            if poll.validity != 0:
              raise EL406DeviceError(poll.validity, get_error_message(poll.validity))
            result = poll.raw_response
            break

          if poll.state == STATE_RUNNING:
            logger.debug("Step in progress (state=Running), continuing poll...")
          elif poll.state == STATE_PAUSED:
            logger.warning("Step is paused (state=3)")
          elif poll.status == 0:
            logger.debug("Done (unknown state=%d, status=0)", poll.state)
            result = poll.raw_response
            break
          else:
            logger.debug("Unknown state=%d, status=%d, continuing...", poll.state, poll.status)
        else:
          raise AssertionError("unreachable")
      if scope.cancel_called:
        raise TimeoutError(f"Timeout waiting for step completion after {timeout}s")
      return result

    return await self._run_locked_transaction(exchange_step)
