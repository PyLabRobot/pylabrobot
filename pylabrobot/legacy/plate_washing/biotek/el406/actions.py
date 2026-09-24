"""EL406 action and control methods.

This module contains the mixin class for action/control operations on the
BioTek EL406 plate washer (reset, home, pause, resume, etc.).
"""

from __future__ import annotations

import logging

from .communication import LONG_READ_TIMEOUT
from .enums import (
  EL406Motor,
  EL406MotorHomeType,
  EL406StepType,
  EL406WasherManifold,
)
from .protocol import build_framed_message

logger = logging.getLogger(__name__)


class EL406ActionsMixin:
  """Mixin providing action/control methods for the EL406.

  This mixin provides:
  - Abort, pause, resume operations
  - Reset instrument
  - Home/verify motors
  - End-of-batch operations
  - Auto-prime operations
  - Set washer manifold

  Requires:
    self._send_framed_command: Async method for sending framed commands
    self._send_action_command: Async method for sending action commands
  """

  async def _send_framed_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
  ) -> bytes:
    raise NotImplementedError

  async def _send_action_command(
    self,
    framed_message: bytes,
    timeout: float | None = None,
  ) -> bytes:
    raise NotImplementedError

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
