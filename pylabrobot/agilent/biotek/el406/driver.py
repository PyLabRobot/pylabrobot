"""EL406 Driver — owns FTDI I/O, connection lifecycle, and device-level operations.

Protocol: 38400 baud, 8N2, no flow control, binary LE framing.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NamedTuple, TypedDict, TypeVar

from pylabrobot.device import Driver
from pylabrobot.io.binary import Reader
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Plate

from .enums import (
  EL406Motor,
  EL406MotorHomeType,
  EL406Sensor,
  EL406StepType,
  EL406SyringeManifold,
  EL406WasherManifold,
)
from .error_codes import get_error_message
from .errors import EL406CommunicationError, EL406DeviceError
from .helpers import plate_to_wire_byte
from .protocol import build_framed_message

logger = logging.getLogger(__name__)

LONG_READ_TIMEOUT = 120.0  # seconds, for long operations (wash cycles can take >30s)

STATE_INITIAL = 1
STATE_RUNNING = 2
STATE_PAUSED = 3
STATE_STOPPED = 4


class DevicePollResult(NamedTuple):
  """Parsed result from a STATUS_POLL response."""

  validity: int
  state: int
  status: int
  raw_response: bytes


class EL406Driver(Driver):
  """FTDI-based driver for the BioTek EL406 plate washer.

  Owns the USB connection, low-level protocol framing, command serialization,
  batch management, and device-level operations (reset, home, pause, etc.).
  """

  def __init__(
    self,
    timeout: float = 15.0,
    device_id: str | None = None,
  ) -> None:
    super().__init__()
    self.timeout = timeout
    self._device_id = device_id
    self.io: FTDI | None = None
    self._command_lock: asyncio.Lock | None = None
    self._in_batch: bool = False

  async def setup(self, skip_reset: bool = False) -> None:
    """Set up communication with the EL406.

    Configures the FTDI USB interface with the correct parameters:
    - 38400 baud
    - 8 data bits, 2 stop bits, no parity (8N2)
    - No flow control (disabled)

    If ``self.io`` is already set (e.g. injected mock for testing),
    it is used as-is and ``setup()`` is not called on it again.

    Args:
      skip_reset: If True, skip the instrument reset step.

    Raises:
      RuntimeError: If pylibftdi is not installed or communication fails.
    """
    self._command_lock = asyncio.Lock()

    logger.info("EL406Driver setting up")
    logger.info("  Timeout: %.1f seconds", self.timeout)

    if self.io is None:
      self.io = FTDI(human_readable_device_name="BioTek EL406", device_id=self._device_id)
      await self.io.setup()

    # Configure serial parameters
    logger.debug("Configuring serial parameters...")
    try:
      await self.io.set_baudrate(38400)
      await self.io.set_line_property(8, 2, 0)  # 8 data bits, 2 stop bits, no parity
      logger.info("  Serial: 38400 baud, 8N2")

      SIO_DISABLE_FLOW_CTRL = 0x0
      await self.io.set_flowctrl(SIO_DISABLE_FLOW_CTRL)
      logger.info("  Flow control: NONE")

      await self.io.set_rts(True)
      await self.io.set_dtr(True)
      logger.debug("  RTS and DTR enabled")
    except Exception as e:
      await self.io.stop()
      self.io = None
      raise EL406CommunicationError(
        f"Failed to configure FTDI device: {e}",
        operation="configure",
        original_error=e,
      ) from e

    # Purge buffers
    logger.debug("Purging TX/RX buffers...")
    await self._purge_buffers()

    # Test communication
    logger.info("Testing communication with device...")
    try:
      await self._test_communication()
      logger.info("  Communication test: PASSED")
    except Exception as e:
      logger.error("  Communication test: FAILED - %s", e)
      raise

    if not skip_reset:
      logger.info("Performing full instrument reset...")
      await self.reset()
      logger.info("  Instrument reset: DONE")

    logger.info("EL406Driver setup complete")

  async def stop(self) -> None:
    """Close the FTDI connection."""
    logger.info("EL406Driver stopping")
    if self.io is not None:
      await self.io.stop()
      self.io = None

  def serialize(self) -> dict:
    """Serialize driver configuration."""
    return {
      **super().serialize(),
      "timeout": self.timeout,
      "device_id": self._device_id,
    }

  # ---------------------------------------------------------------------------
  # Low-level I/O
  # ---------------------------------------------------------------------------

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

  async def _wait_for_ack(self, timeout: float, t0: float) -> None:
    """Poll device for ACK byte within the remaining timeout window.

    Args:
      timeout: Total timeout budget in seconds.
      t0: Start timestamp (from ``time.monotonic()``).

    Raises:
      RuntimeError: If device sends NAK.
      TimeoutError: If no ACK within timeout.
    """
    assert self.io is not None
    while time.monotonic() - t0 < timeout:
      byte = await self.io.read(1)
      if byte:
        if byte[0] == 0x15:  # NAK
          raise RuntimeError(
            f"Device rejected command (NAK). Response: {byte!r}. "
            "This may indicate an invalid command, bad parameters, or device busy state."
          )
        if byte[0] == 0x06:  # ACK
          return
      await asyncio.sleep(0.01)
    raise TimeoutError("Timeout waiting for ACK")

  async def _read_exact_bytes(self, count: int, timeout: float, t0: float) -> bytes:
    """Read exactly *count* bytes from the device, polling until done or timeout.

    Args:
      count: Number of bytes to read.
      timeout: Total timeout budget in seconds.
      t0: Start timestamp (from ``time.monotonic()``).

    Returns:
      Bytes read (may be shorter than *count* if timeout is reached).
    """
    assert self.io is not None
    buf = b""
    while len(buf) < count and time.monotonic() - t0 < timeout:
      chunk = await self.io.read(count - len(buf))
      if chunk:
        buf += chunk
      else:
        await asyncio.sleep(0.01)
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

  # ---------------------------------------------------------------------------
  # Command sending
  # ---------------------------------------------------------------------------

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
        await asyncio.sleep(0.001)  # Small delay between header and data
        await self._write_to_device(data)
        logger.debug("Sent data: %s", data.hex())
      logger.debug("Sent framed: %s", framed_message.hex())

      # Read full response: ACK + 11-byte header + variable data
      await self._wait_for_ack(timeout, time.monotonic())
      result = bytes([0x06])

      # Fresh timestamp after ACK — header + data share a single timeout budget.
      t0 = time.monotonic()
      resp_header = await self._read_exact_bytes(11, timeout, t0)

      if len(resp_header) == 11:
        result += resp_header
        # Parse data length from header bytes 7-8 (little-endian)
        data_len = Reader(resp_header[7:]).u16()
        response_data = await self._read_exact_bytes(data_len, timeout, t0)
        result += response_data
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

    async with self._command_lock:
      await self._purge_buffers()

      # Send header and data separately (matches _send_framed_command protocol)
      header = framed_message[:11]
      data = framed_message[11:] if len(framed_message) > 11 else b""

      await self._write_to_device(header)
      if data:
        await asyncio.sleep(0.001)
        await self._write_to_device(data)
      logger.debug("Sent action command: %s", framed_message.hex())

      t0 = time.monotonic()

      # Step 1: Wait for ACK (short timeout)
      await self._wait_for_ack(min(timeout, self.timeout), t0)
      logger.debug("Got ACK, waiting for completion...")

      # Step 2: Wait for completion frame (11-byte header + data)
      header = await self._read_exact_bytes(11, timeout, t0)
      if len(header) < 11:
        raise TimeoutError(f"Timeout waiting for completion header (got {len(header)} bytes)")

      # Parse data length and read remaining data
      data_len = Reader(header[7:]).u16()
      data = await self._read_exact_bytes(data_len, timeout, t0)

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
        await asyncio.sleep(0.001)
        await self._write_to_device(msg_data)
        logger.debug("Sent query data: %s", msg_data.hex())

      # Wait for ACK
      try:
        await self._wait_for_ack(timeout, time.monotonic())
      except RuntimeError as e:
        raise RuntimeError(
          f"Device rejected command 0x{command:04X} (NAK). Check command code and parameters."
        ) from e
      except TimeoutError as e:
        raise TimeoutError(f"Timeout waiting for ACK (command 0x{command:04X})") from e

      t0 = time.monotonic()
      # Read 11-byte response header (shares timeout budget with data)
      resp_header = await self._read_exact_bytes(11, timeout, t0)
      if len(resp_header) < 11:
        raise TimeoutError(f"Timeout reading response header (got {len(resp_header)}/11 bytes)")

      logger.debug("Response header: %s", resp_header.hex())

      # Parse data length from header bytes 7-8 (little-endian)
      data_len = Reader(resp_header[7:]).u16()
      logger.debug("Response data length: %d", data_len)

      # Read data bytes
      response_data = await self._read_exact_bytes(data_len, timeout, t0)
      if len(response_data) < data_len:
        raise TimeoutError(
          f"Timeout reading response data (got {len(response_data)}/{data_len} bytes)"
        )

      logger.debug("Response data: %s", response_data.hex())
      return response_data

  # ---------------------------------------------------------------------------
  # Polling
  # ---------------------------------------------------------------------------

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
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
      poll = await self._poll_device_state()
      if poll.state != STATE_RUNNING:
        return
      await asyncio.sleep(poll_interval)
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
    await asyncio.sleep(0.5)

    # 4. Poll for completion
    t0 = time.monotonic()
    poll_count = 0

    logger.debug("Starting polling loop...")

    while time.monotonic() - t0 < timeout:
      await asyncio.sleep(poll_interval)
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

    raise TimeoutError(f"Timeout waiting for step completion after {timeout}s")

  # ---------------------------------------------------------------------------
  # Batch management
  # ---------------------------------------------------------------------------

  @asynccontextmanager
  async def batch(self, plate: Plate) -> AsyncIterator[None]:
    """Context manager for batching step commands.

    Each step command (manifold_wash, syringe_prime, etc.) automatically wraps
    its execution in a batch. Use this context manager to group multiple step
    commands into a single batch, avoiding repeated start/cleanup cycles.

    If already inside a batch, this is a no-op passthrough.

    Args:
      plate: PLR Plate to configure for this batch.

    Example:
      >>> async with driver.batch(plate_96):
      ...     await driver._send_step_command(framed_cmd)
    """
    if self._in_batch:
      yield
      return

    self._in_batch = True
    try:
      await self.start_batch(plate_to_wire_byte(plate))
      yield
    finally:
      try:
        await self.cleanup_after_protocol()
      finally:
        self._in_batch = False

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

  # ---------------------------------------------------------------------------
  # Device-level operations
  # ---------------------------------------------------------------------------

  async def abort(
    self,
    step_type: EL406StepType | None = None,
  ) -> None:
    """Abort a running operation.

    Args:
      step_type: Optional step type to abort. If None, aborts current operation.

    Raises:
      RuntimeError: If device not initialized.
      TimeoutError: If timeout waiting for ACK response.
    """
    logger.info(
      "Aborting %s",
      f"step type {step_type.name}" if step_type is not None else "current operation",
    )

    step_type_value = step_type.value if step_type is not None else 0
    data = bytes([step_type_value])
    framed_command = build_framed_message(command=0x89, data=data)
    await self._send_framed_command(framed_command)

  async def pause(self) -> None:
    """Pause a running operation."""
    logger.info("Pausing operation")
    framed_command = build_framed_message(command=0x8A)
    await self._send_framed_command(framed_command)

  async def resume(self) -> None:
    """Resume a paused operation."""
    logger.info("Resuming operation")
    framed_command = build_framed_message(command=0x8B)
    await self._send_framed_command(framed_command)

  async def reset(self) -> None:
    """Reset the instrument to a known state."""
    logger.info("Resetting instrument")
    framed_command = build_framed_message(command=0x70)
    await self._send_action_command(framed_command, timeout=LONG_READ_TIMEOUT)
    logger.info("Instrument reset complete")

  async def _perform_end_of_batch(self) -> None:
    """Perform end-of-batch activities - sends completion marker.

    NOTE: This command (140) is just a completion marker and does NOT:
    - Stop the pump
    - Home the syringes

    For a complete cleanup after a protocol, use cleanup_after_protocol() instead.
    """
    logger.info("Performing end-of-batch activities (completion marker)")
    framed_command = build_framed_message(command=0x8C)
    await self._send_action_command(framed_command, timeout=60.0)
    logger.info("End-of-batch marker sent")

  async def cleanup_after_protocol(self) -> None:
    """Complete cleanup after running a protocol.

    This method performs the full cleanup sequence that the original BioTek
    software does after all protocol steps complete:
    1. Home the syringes (XYZ motors)
    2. Send end-of-batch completion marker

    This is the recommended way to end a protocol run.

    Example:
      >>> # Run protocol steps
      >>> await backend.syringe_prime("A", 1000, 5, 2)
      >>> await backend.syringe_prime("B", 1000, 5, 2)
      >>> # Then cleanup
      >>> await backend.cleanup_after_protocol()
    """
    logger.info("Starting post-protocol cleanup")

    # Step 1: Home syringes
    logger.info("  Homing motors...")
    await self.home_motors(EL406MotorHomeType.HOME_XYZ_MOTORS)

    # Step 2: Send end-of-batch marker
    logger.info("  Sending end-of-batch marker...")
    await self._perform_end_of_batch()

    logger.info("Post-protocol cleanup complete")

  async def home_motors(
    self,
    home_type: EL406MotorHomeType,
    motor: EL406Motor | None = None,
  ) -> None:
    """Home or verify motor positions."""
    logger.info(
      "Home/verify motors: type=%s, motor=%s",
      home_type.name,
      motor.name if motor is not None else "default(0)",
    )

    motor_num = motor.value if motor is not None else 0
    data = bytes([home_type.value, motor_num])
    framed_command = build_framed_message(command=0xC8, data=data)
    await self._send_action_command(framed_command, timeout=120.0)
    logger.info("Motors homed")

  async def set_washer_manifold(self, manifold: EL406WasherManifold) -> None:
    """Set the washer manifold type."""
    logger.info("Setting washer manifold to: %s", manifold.name)
    data = bytes([manifold.value])
    framed_command = build_framed_message(command=0xD9, data=data)
    await self._send_framed_command(framed_command)
    logger.info("Washer manifold set to: %s", manifold.name)

  # ---------------------------------------------------------------------------
  # Queries
  # ---------------------------------------------------------------------------

  @staticmethod
  def _extract_payload_byte(response_data: bytes) -> int:
    """Extract the first payload byte, handling optional 2-byte header prefix."""
    return response_data[2] if len(response_data) > 2 else response_data[0]

  _E = TypeVar("_E", bound=enum.Enum)

  async def _query_enum(self, command: int, enum_cls: type[_E], label: str) -> _E:
    """Send a framed query and parse the response byte as an *enum_cls* member."""
    logger.info("Querying %s", label)
    response_data = await self._send_framed_query(command)
    logger.debug("%s response data: %s", label.capitalize(), response_data.hex())
    value_byte = self._extract_payload_byte(response_data)

    try:
      result = enum_cls(value_byte)
    except ValueError:
      logger.warning("Unknown %s: %d (0x%02X)", label, value_byte, value_byte)
      raise ValueError(
        f"Unknown {label}: {value_byte} (0x{value_byte:02X}). "
        f"Valid types: {[m.name for m in enum_cls]}"
      ) from None

    logger.info("%s: %s (0x%02X)", label.capitalize(), result.name, result.value)
    return result

  async def request_washer_manifold(self) -> EL406WasherManifold:
    """Query the installed washer manifold type."""
    return await self._query_enum(
      command=0xD8, enum_cls=EL406WasherManifold, label="washer manifold type"
    )

  async def request_syringe_manifold(self) -> EL406SyringeManifold:
    """Query the installed syringe manifold type."""
    return await self._query_enum(
      command=0xBB, enum_cls=EL406SyringeManifold, label="syringe manifold type"
    )

  async def request_serial_number(self) -> str:
    """Query the product serial number."""
    logger.info("Querying product serial number")
    response_data = await self._send_framed_query(command=0x0100)
    serial_number = response_data[2:].decode("ascii", errors="ignore").strip().rstrip("\x00")
    logger.info("Product serial number: %s", serial_number)
    return serial_number

  async def request_sensor_enabled(self, sensor: EL406Sensor) -> bool:
    """Query whether a specific sensor is enabled."""
    logger.info("Querying sensor enabled status: %s", sensor.name)
    response_data = await self._send_framed_query(command=0xD2, data=bytes([sensor.value]))
    logger.debug("Sensor enabled response data: %s", response_data.hex())
    enabled = bool(self._extract_payload_byte(response_data))
    logger.info("Sensor %s enabled: %s", sensor.name, enabled)
    return enabled

  class SyringeBoxInfo(TypedDict):
    box_type: int
    box_size: int
    installed: bool

  async def request_syringe_box_info(self) -> SyringeBoxInfo:
    """Get syringe box information."""
    logger.info("Querying syringe box info")
    response_data = await self._send_framed_query(command=0xF6)
    logger.debug("Syringe box info response data: %s", response_data.hex())

    box_type = self._extract_payload_byte(response_data)
    box_size = (
      response_data[3]
      if len(response_data) > 3
      else (response_data[1] if len(response_data) > 1 else 0)
    )
    installed = box_type != 0

    info = self.SyringeBoxInfo(box_type=box_type, box_size=box_size, installed=installed)
    logger.info("Syringe box info: %s", info)
    return info

  async def request_peristaltic_installed(self, selector: int) -> bool:
    """Check if a peristaltic pump is installed."""
    if selector < 0 or selector > 1:
      raise ValueError(f"Invalid selector {selector}. Must be 0 (primary) or 1 (secondary).")

    logger.info("Querying peristaltic pump installed: selector=%d", selector)
    response_data = await self._send_framed_query(command=0x0104, data=bytes([selector]))
    logger.debug("Peristaltic installed response data: %s", response_data.hex())

    installed = bool(self._extract_payload_byte(response_data))

    logger.info("Peristaltic pump %d installed: %s", selector, installed)
    return installed

  class InstrumentSettings(TypedDict):
    washer_manifold: EL406WasherManifold
    syringe_manifold: EL406SyringeManifold
    syringe_box: "EL406Driver.SyringeBoxInfo"
    peristaltic_pump_1: bool
    peristaltic_pump_2: bool

  async def request_instrument_settings(self) -> InstrumentSettings:
    """Get current instrument hardware configuration."""
    logger.info("Querying instrument settings from hardware")

    washer_manifold = await self.request_washer_manifold()
    syringe_manifold = await self.request_syringe_manifold()
    syringe_box = await self.request_syringe_box_info()
    peristaltic_1 = await self.request_peristaltic_installed(0)
    peristaltic_2 = await self.request_peristaltic_installed(1)

    settings = self.InstrumentSettings(
      washer_manifold=washer_manifold,
      syringe_manifold=syringe_manifold,
      syringe_box=syringe_box,
      peristaltic_pump_1=peristaltic_1,
      peristaltic_pump_2=peristaltic_2,
    )
    logger.info("Instrument settings: %s", settings)
    return settings

  class SelfCheckResult(TypedDict):
    success: bool
    error_code: int
    message: str

  async def run_self_check(self) -> SelfCheckResult:
    """Run instrument self-check diagnostics."""
    logger.info("Running instrument self-check")
    response_data = await self._send_framed_query(command=0x95, timeout=LONG_READ_TIMEOUT)
    logger.debug("Self-check response data: %s", response_data.hex())
    error_code = self._extract_payload_byte(response_data)
    success = error_code == 0

    message = "Self-check passed" if success else f"Self-check failed (error code: {error_code})"
    result = self.SelfCheckResult(success=success, error_code=error_code, message=message)
    logger.info("Self-check result: %s", result["message"])
    return result
