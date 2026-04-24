"""EL406 low-level communication methods.

This module contains the mixin class for low-level USB/FTDI communication
with the BioTek EL406 plate washer.
"""

from __future__ import annotations

import logging
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

  async def _wait_for_ack(self, timeout: float) -> None:
    """Poll device for ACK byte within the timeout window.

    Args:
      timeout: Timeout budget in seconds.

    Raises:
      RuntimeError: If device sends NAK.
      TimeoutError: If no ACK within timeout.
    """
    assert self.io is not None
    with anyio.fail_after(timeout):
      while True:
        byte = await self.io.read(1)
        if byte:
          if byte[0] == 0x15:  # NAK
            raise RuntimeError(
              f"Device rejected command (NAK). Response: {byte!r}. "
              "This may indicate an invalid command, bad parameters, or device busy state."
            )
          if byte[0] == 0x06:  # ACK
            return
        await anyio.sleep(0.01)

  async def _read_exact_bytes(self, count: int) -> bytes:
    """Read exactly *count* bytes from the device, polling until done.

    Args:
      count: Number of bytes to read.

    Returns:
      Bytes read.
    """
    assert self.io is not None
    buf = b""
    while len(buf) < count:
      chunk = await self.io.read(count - len(buf))
      if chunk:
        buf += chunk
      else:
        await anyio.sleep(0.01)
    return buf

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
    response = await self._send_framed_command(start_step_cmd, timeout=self.timeout)
    logger.debug("START_STEP sent, response: %s", response.hex())

  async def _send_framed_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
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

    async with self._command_lock:
      await self._purge_buffers()

      # Send header and data separately
      header = framed_message[:11]
      data = framed_message[11:] if len(framed_message) > 11 else b""

      await self._write_to_device(header)
      logger.debug("Sent header: %s", header.hex())

      if data:
        await anyio.sleep(0.001)  # Small delay between header and data
        await self._write_to_device(data)
        logger.debug("Sent data: %s", data.hex())
      logger.debug("Sent framed: %s", framed_message.hex())

      # Read full response: ACK + 11-byte header + variable data
      await self._wait_for_ack(timeout)
      result = bytes([0x06])

      try:
        with anyio.fail_after(timeout):
          resp_header = await self._read_exact_bytes(11)

          if len(resp_header) == 11:
            result += resp_header
            # Parse data length from header bytes 7-8 (little-endian)
            data_len = Reader(resp_header[7:]).u16()
            response_data = await self._read_exact_bytes(data_len)
            result += response_data
            logger.debug("Full response: %s (%d bytes)", result.hex(), len(result))
          else:
            logger.debug("ACK-only response (no frame): %s", result.hex())
      except TimeoutError:
        raise TimeoutError("Timeout reading response from EL406") from None

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

    async with self._command_lock:
      await self._purge_buffers()

      # Send header and data separately (matches _send_framed_command protocol)
      header = framed_message[:11]
      data = framed_message[11:] if len(framed_message) > 11 else b""

      await self._write_to_device(header)
      if data:
        await anyio.sleep(0.001)
        await self._write_to_device(data)
      logger.debug("Sent action command: %s", framed_message.hex())

      try:
        with anyio.fail_after(timeout):
          # Step 1: Wait for ACK (short timeout)
          await self._wait_for_ack(min(timeout, self.timeout))
          logger.debug("Got ACK, waiting for completion...")

          # Step 2: Wait for completion frame (11-byte header + data)
          header = await self._read_exact_bytes(11)
          if len(header) < 11:
            raise TimeoutError(f"Timeout waiting for completion header (got {len(header)} bytes)")

          # Parse data length and read remaining data
          data_len = Reader(header[7:]).u16()
          data = await self._read_exact_bytes(data_len)
      except TimeoutError:
        raise TimeoutError("Timeout waiting for completion frame from EL406") from None

      result = header + data

      logger.debug("Completion frame: %s (%d bytes)", result.hex(), len(result))

      # Parse and log result
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

    async with self._command_lock:
      await self._purge_buffers()

      # Split header and data
      msg_header = framed_message[:11]
      msg_data = framed_message[11:] if len(framed_message) > 11 else b""

      await self._write_to_device(msg_header)
      logger.debug("Sent query header 0x%04X: %s", command, msg_header.hex())

      if msg_data:
        await anyio.sleep(0.001)
        await self._write_to_device(msg_data)
        logger.debug("Sent query data: %s", msg_data.hex())

      # Wait for ACK
      try:
        await self._wait_for_ack(timeout)
      except RuntimeError as e:
        raise RuntimeError(
          f"Device rejected command 0x{command:04X} (NAK). Check command code and parameters."
        ) from e
      except TimeoutError as e:
        raise TimeoutError(f"Timeout waiting for ACK (command 0x{command:04X})") from e

      try:
        with anyio.fail_after(timeout):
          # Read 11-byte response header (shares timeout budget with data)
          resp_header = await self._read_exact_bytes(11)
          if len(resp_header) < 11:
            raise TimeoutError(f"Timeout reading response header (got {len(resp_header)}/11 bytes)")

          logger.debug("Response header: %s", resp_header.hex())

          # Parse data length from header bytes 7-8 (little-endian)
          data_len = Reader(resp_header[7:]).u16()
          logger.debug("Response data length: %d", data_len)

          # Read data bytes
          response_data = await self._read_exact_bytes(data_len)
      except TimeoutError:
        raise TimeoutError("Timeout reading response from EL406") from None
      if len(response_data) < data_len:
        raise TimeoutError(
          f"Timeout reading response data (got {len(response_data)}/{data_len} bytes)"
        )

      logger.debug("Response data: %s", response_data.hex())
      return response_data

  async def _poll_device_state(self) -> DevicePollResult:
    """Send one STATUS_POLL and return the parsed device state.

    Returns:
      DevicePollResult with validity, state, status, and raw_response.

    Raises:
      EL406CommunicationError: If poll response is too short to parse.
    """
    poll_command = build_framed_message(command=0x92)
    poll_response = await self._send_framed_command(poll_command, timeout=self.timeout)
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
    try:
      with anyio.fail_after(timeout):
        while True:
          poll = await self._poll_device_state()
          if poll.state != STATE_RUNNING:
            return
          await anyio.sleep(poll_interval)
    except TimeoutError:
      raise TimeoutError(
        f"Device still busy (STATE_RUNNING) after {timeout}s waiting for readiness"
      )

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
    if self.io is None:
      raise RuntimeError("Device not initialized")

    if timeout is None:
      timeout = LONG_READ_TIMEOUT

    logger.debug("Starting step command with timeout=%ss", timeout)

    # 1. Wait for device to be ready (not RUNNING)
    await self._wait_until_ready(timeout=min(timeout, self.timeout))

    # 2. Send the step command
    logger.debug("Sending step command: %s", framed_message.hex())
    response = await self._send_framed_command(framed_message, timeout=min(timeout, self.timeout))
    logger.debug("Step command sent, got initial response: %s", response.hex())

    # 3. Initial delay before polling
    await anyio.sleep(0.5)

    # 4. Poll for completion
    poll_count = 0
    try:
      with anyio.fail_after(timeout):
        while True:
          await anyio.sleep(poll_interval)
          poll_count += 1

          poll = await self._poll_device_state()
          logger.debug("Poll #%d: %d bytes", poll_count, len(poll.raw_response))

          if poll.state in (STATE_INITIAL, STATE_STOPPED):
            logger.debug("Step completed (state=%d) after %d polls", poll.state, poll_count)
            if poll.validity != 0:
              raise EL406DeviceError(poll.validity, get_error_message(poll.validity))
            return poll.raw_response

          if poll.state == STATE_RUNNING:
            logger.debug("Step in progress (state=Running), continuing poll...")
          elif poll.state == STATE_PAUSED:
            logger.warning("Step is paused (state=3)")
          elif poll.status == 0:
            # Unknown state with status=0 means done
            logger.debug("Done (unknown state=%d, status=0)", poll.state)
            return poll.raw_response
          else:
            logger.debug("Unknown state=%d, status=%d, continuing...", poll.state, poll.status)
    except TimeoutError:
      raise TimeoutError(f"Timeout waiting for step completion after {timeout}s")
