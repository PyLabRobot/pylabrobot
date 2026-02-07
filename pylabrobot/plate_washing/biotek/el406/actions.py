"""EL406 action and control methods.

This module contains the mixin class for action/control operations on the
BioTek EL406 plate washer (reset, home, pause, resume, etc.).
"""

from __future__ import annotations

import logging

from .constants import (
  ABORT_COMMAND,
  END_OF_BATCH_COMMAND,
  HOME_VERIFY_MOTORS_COMMAND,
  LONG_READ_TIMEOUT,
  PAUSE_COMMAND,
  RESET_COMMAND,
  RESUME_COMMAND,
  SET_WASHER_MANIFOLD_COMMAND,
  VACUUM_PUMP_CONTROL_COMMAND,
)
from .enums import (
  EL406Motor,
  EL406MotorHomeType,
  EL406StepType,
  EL406WasherManifold,
)
from .protocol import build_framed_message

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")


class EL406ActionsMixin:
  """Mixin providing action/control methods for the EL406.

  This mixin provides:
  - Abort, pause, resume operations
  - Reset instrument
  - Home/verify motors
  - Vacuum pump control
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
    framed_command = build_framed_message(ABORT_COMMAND, data)
    await self._send_framed_command(framed_command)

  async def pause(self) -> None:
    """Pause a running operation."""
    logger.info("Pausing operation")
    framed_command = build_framed_message(PAUSE_COMMAND)
    await self._send_framed_command(framed_command)

  async def resume(self) -> None:
    """Resume a paused operation."""
    logger.info("Resuming operation")
    framed_command = build_framed_message(RESUME_COMMAND)
    await self._send_framed_command(framed_command)

  async def reset(self) -> None:
    """Reset the instrument to a known state."""
    logger.info("Resetting instrument")
    framed_command = build_framed_message(RESET_COMMAND)
    await self._send_action_command(framed_command, timeout=LONG_READ_TIMEOUT)
    logger.info("Instrument reset complete")

  async def _perform_end_of_batch(self) -> None:
    """Perform end-of-batch activities - sends completion marker.

    NOTE: This command (140) is just a completion marker and does NOT:
    - Stop the pump
    - Home the syringes

    For a complete cleanup after a protocol, use cleanup_after_protocol() instead,
    or manually call:
    1. set_vacuum_pump(False) - to stop the pump
    2. home_motors() - to return syringes to home position
    """
    logger.info("Performing end-of-batch activities (completion marker)")
    framed_command = build_framed_message(END_OF_BATCH_COMMAND)
    await self._send_action_command(framed_command, timeout=60.0)
    logger.info("End-of-batch marker sent")

  async def cleanup_after_protocol(self) -> None:
    """Complete cleanup after running a protocol.

    This method performs the full cleanup sequence that the original BioTek
    software does after all protocol steps complete:
    1. Stop the vacuum/peristaltic pump
    2. Home the syringes (XYZ motors)
    3. Send end-of-batch completion marker

    This is the recommended way to end a protocol run.

    Example:
      >>> # Run protocol steps
      >>> await backend.syringe_prime("A", 1000, 5, 2)
      >>> await backend.syringe_prime("B", 1000, 5, 2)
      >>> # Then cleanup
      >>> await backend.cleanup_after_protocol()
    """
    logger.info("Starting post-protocol cleanup")

    # Step 1: Stop the pump
    logger.info("  Stopping vacuum pump...")
    await self.set_vacuum_pump(False)

    # Step 2: Home syringes
    logger.info("  Homing motors...")
    await self.home_motors(EL406MotorHomeType.HOME_XYZ_MOTORS)

    # Step 3: Send end-of-batch marker
    logger.info("  Sending end-of-batch marker...")
    await self._perform_end_of_batch()

    logger.info("Post-protocol cleanup complete")

  async def set_vacuum_pump(self, enabled: bool) -> None:
    """Control the vacuum/peristaltic pump on or off.

    This sends command 299 (LeaveVacuumPumpOn) to control the pump state.
    After syringe_prime or other pump operations, call this with
    enabled=False to stop the pump.

    Args:
      enabled: True to turn pump ON, False to turn pump OFF.

    Raises:
      RuntimeError: If device not initialized.
      TimeoutError: If timeout waiting for response.

    Example:
      >>> # After syringe prime, stop the pump
      >>> await backend.syringe_prime("A", 1000, 5, 2)
      >>> await backend.set_vacuum_pump(False)  # STOP THE PUMP
      >>> await backend.home_motors(EL406MotorHomeType.HOME_XYZ_MOTORS)
    """
    state_str = "ON" if enabled else "OFF"
    logger.info("Setting vacuum pump: %s", state_str)

    # Command 299 with 2-byte parameter (little-endian short): 1=on, 0=off
    data = bytes([1 if enabled else 0, 0x00])
    framed_command = build_framed_message(VACUUM_PUMP_CONTROL_COMMAND, data)
    await self._send_framed_command(framed_command)
    logger.info("Vacuum pump set to %s", state_str)

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
    framed_command = build_framed_message(HOME_VERIFY_MOTORS_COMMAND, data)
    await self._send_action_command(framed_command, timeout=120.0)
    logger.info("Motors homed")

  async def set_washer_manifold(self, manifold: EL406WasherManifold) -> None:
    """Set the washer manifold type."""
    logger.info("Setting washer manifold to: %s", manifold.name)
    data = bytes([manifold.value])
    framed_command = build_framed_message(SET_WASHER_MANIFOLD_COMMAND, data)
    await self._send_framed_command(framed_command)
    logger.info("Washer manifold set to: %s", manifold.name)
