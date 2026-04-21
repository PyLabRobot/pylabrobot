"""BioTek EL406 plate washer backend.

This module provides the backend implementation for the BioTek EL406
plate washer, communicating via FTDI USB serial interface.

Protocol Details:
- Serial: 38400 baud, 8 data bits, 2 stop bits, no parity
- Flow control: disabled (no flow control)
- ACK byte: 0x06
- Commands are binary with little-endian encoding
- Read timeout: 15000ms, Write timeout: 5000ms
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.ftdi import FTDI
from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources import Plate

from .actions import EL406ActionsMixin
from .communication import EL406CommunicationMixin
from .errors import EL406CommunicationError
from .helpers import plate_to_wire_byte
from .queries import EL406QueriesMixin
from .steps import EL406StepsMixin

logger = logging.getLogger(__name__)


class ExperimentalBioTekEL406Backend(
  EL406CommunicationMixin,
  EL406QueriesMixin,
  EL406ActionsMixin,
  EL406StepsMixin,
  MachineBackend,
):
  """Backend for BioTek EL406 plate washer.

  Communicates with the EL406 via FTDI USB interface.

  Attributes:
    timeout: Default timeout for operations in seconds.

  Example:
    >>> backend = BioTekEL406Backend()
    >>> await backend.setup()
    >>> await backend.peristaltic_prime(plate, volume=300.0, flow_rate="High")
    >>> await backend.manifold_wash(plate, cycles=3)
  """

  def __init__(
    self,
    timeout: float = 15.0,
    device_id: str | None = None,
  ) -> None:
    """Initialize the EL406 backend.

    Args:
      timeout: Default timeout for operations in seconds.
      device_id: FTDI device serial number for explicit connection.
    """
    super().__init__()
    self.timeout = timeout
    self._device_id = device_id
    self.io: FTDI | None = None
    self._command_lock: anyio.Lock | None = None
    self._in_batch: bool = False

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, *, skip_reset: bool = False):
    """Set up communication with the EL406.

    Configures the FTDI USB interface with the correct parameters:
    - 38400 baud
    - 8 data bits, 2 stop bits, no parity (8N2)
    - No flow control (disabled)

    If ``self.io`` is already set (e.g. injected mock for testing),
    it is used as-is.

    Note: This does NOT start a batch. Use ``batch()`` or call step commands
    directly (they auto-batch).

    Args:
      stack: The AsyncExitStack to register cleanups with.
      skip_reset: If True, skip the instrument reset step.

    Raises:
      RuntimeError: If pylibftdi is not installed or communication fails.
    """
    await super()._enter_lifespan(stack)

    self._command_lock = anyio.Lock()

    logger.info("BioTekEL406Backend setting up")
    logger.info("  Timeout: %.1f seconds", self.timeout)

    if self.io is None:
      self.io = FTDI(human_readable_device_name="BioTek EL406", device_id=self._device_id)

    @stack.callback
    def _cleanup():
      self.io = None

    await stack.enter_async_context(self.io)

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

    logger.info("BioTekEL406Backend setup complete")

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
      >>> async with backend.batch(plate_96):
      ...     await backend.manifold_prime(plate_96, volume=5000)
      ...     await backend.manifold_wash(plate_96, cycles=3)
      ...     await backend.syringe_dispense(plate_96, volume=50)
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

  def serialize(self) -> dict:
    """Serialize backend configuration."""
    return {
      **super().serialize(),
      "timeout": self.timeout,
      "device_id": self._device_id,
    }
