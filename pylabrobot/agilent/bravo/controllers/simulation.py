"""Simulation controller for the Bravo.

A software-only substitute for the Bravo hardware that lets protocols and
higher layers run without a physical instrument. No axis move, homing
operation, or head/gripper query touches any transport; positions and state
are tracked entirely in memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Union

from ..errors import BravoError
from ..protocol.commands import LightCommandData
from ..types import (
  ALL_AXES,
  OPEN_GRIPPER_POSITION,
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  HeadType,
  SpeedLevel,
  axis_display_name,
  head_type_code,
)
from .base import AxisMoveInfo, BravoController, FirmwareVersion, JogParams

logger = logging.getLogger(__name__)


@dataclass
class SimulatedAxis:
  """In-memory state tracked for one simulated axis.

  Attributes:
    position: Current position, in mm (or uL for the W axis).
    homed: Whether the axis currently reports itself homed.
    motor_enabled: Whether the axis's motor drive is enabled.
  """

  position: float = 0.0
  homed: bool = False
  motor_enabled: bool = False


class SimulationController(BravoController):
  """Software simulation of the Bravo hardware.

  Tracks axis positions and state in memory, with no transport and no I/O.
  Every axis starts homed at its configured offset, so the simulated
  coordinate system is teachpoint-anchored from construction onward, the
  same as the coordinate system a deck model expects. :meth:`home_axes`
  returns a moved axis to that offset; :meth:`initialize` does the same for
  every axis, so it is idempotent immediately after construction and only
  has an observable effect once something has moved an axis away from its
  offset.
  """

  def __init__(
    self, head_type: HeadType = "96_d_70", homing_offsets: Optional[dict[Axis, float]] = None
  ):
    """Create a simulated controller.

    Args:
      head_type: The pipetting head to simulate as installed. Determines
        what :meth:`read_smart_head_type` and :meth:`read_head_adc` report,
        and can be changed later with :meth:`set_head_type`.
      homing_offsets: The position each axis is homed to, keyed by axis.
        An axis with no entry homes to ``0.0``. Every axis starts at its
        homing offset, already homed, so the simulated coordinate system
        matches the teachpoint coordinate system from the start.
    """
    offsets: dict[Axis, float] = homing_offsets or {}
    self._homing_offsets: dict[Axis, float] = offsets
    self._axes: dict[Axis, SimulatedAxis] = {
      axis: SimulatedAxis(position=offsets.get(axis, 0.0), homed=True) for axis in ALL_AXES
    }
    self._head_type = head_type
    self._gripper_detected = GripperDetectionState.DETECTED
    self._plate_in_gripper = False
    self._plate_sensor_present = False
    self._simulated_scan_height_mm: Optional[float] = None
    self._last_error: Optional[BravoError] = None
    self._lights: Optional[LightCommandData] = None
    self._go_button_pressed = False
    logger.info("SimulationController created (head_type=%s)", head_type)

  # -- Lifecycle --

  def initialize(self) -> None:
    """Bring the simulated controller to a usable state.

    Homes every axis. Every axis already starts homed at its configured
    offset, so calling this immediately after construction changes
    nothing; it only has an observable effect on an axis that has since
    moved away from its offset. No transport is involved: this controller
    performs no I/O.
    """
    self.home_axes(list(ALL_AXES))
    logger.info("Simulation: initialize() complete, all axes homed")

  def ping(self) -> bool:
    return True

  @property
  def is_connected(self) -> bool:
    return True

  # -- Firmware --

  def get_firmware_version(self) -> FirmwareVersion:
    return FirmwareVersion(master="1.2.3", sub1="", sub2="")

  # -- Motion --

  def move(self, moves: list[AxisMoveInfo], wait: bool = True, timeout: float = 30.0) -> None:
    max_duration = 0.0
    for m in moves:
      ax = self._axes[m.axis]
      old_pos = ax.position
      if m.absolute:
        ax.position = m.position
      else:
        ax.position += m.position
      # A velocity-based duration is simulated so W-axis moves (aspirate,
      # dispense, mix) take the wall-clock time their velocity implies,
      # letting liquid-class velocity parameters produce an observable
      # difference in simulation.
      if m.velocity > 0 and wait:
        distance = abs(ax.position - old_pos)
        duration = distance / m.velocity
        max_duration = max(max_duration, duration)
      logger.debug(
        "Simulation: move %s to %.3f (abs=%s, vel=%.3f, accel=%.3f)",
        axis_display_name(m.axis),
        ax.position,
        m.absolute,
        m.velocity,
        m.acceleration,
      )
    if max_duration > 0:
      logger.info("Simulation: waiting %.2fs for move (velocity-based timing)", max_duration)
      time.sleep(max_duration)

  def home_axes(self, axes: list[Axis], *, force: bool = False) -> None:
    for axis in axes:
      self._axes[axis].position = self._homing_offsets.get(axis, 0.0)
      self._axes[axis].homed = True
      logger.debug("Simulation: homed %s", axis_display_name(axis))

  def jog(self, params: JogParams) -> float:
    ax = self._axes[params.axis]
    ax.position += params.max_position
    logger.debug(
      "Simulation: jog %s to %.3f (max_pos=%.3f)",
      axis_display_name(params.axis),
      ax.position,
      params.max_position,
    )
    return ax.position

  def get_position(self, axis: Axis) -> float:
    return self._axes[axis].position

  def is_axis_homed(self, axis: Axis) -> bool:
    return self._axes[axis].homed

  def get_park_position(self, axis: Axis) -> float:
    return self._homing_offsets.get(axis, 0.0)

  # -- Motor control --

  def enable_motor(self, axis: Axis) -> None:
    self._axes[axis].motor_enabled = True
    logger.debug("Simulation: enable_motor(%s)", axis_display_name(axis))

  def disable_motor(self, axis: Axis) -> None:
    self._axes[axis].motor_enabled = False
    logger.debug("Simulation: disable_motor(%s)", axis_display_name(axis))

  def reset_faults(self, axes: list[Axis]) -> None:
    logger.debug("Simulation: reset_faults [no-op]")

  # -- Device state --

  def query_state(self) -> DeviceStateFlag:
    return DeviceStateFlag(0)

  def is_go_button_pressed(self) -> bool:
    return self._go_button_pressed

  def clear_go_button(self) -> None:
    self._go_button_pressed = False

  # -- Lights --

  def set_light(self, command: LightCommandData) -> None:
    self._lights = command
    logger.debug("Simulation: set_light(%s)", command)

  def clear_lights(self) -> None:
    self._lights = None

  # -- Head detection --

  # ADC values for the head types known to the resistor-divider table. Other
  # head types have no sourced value and fall back to the 96_d_70 reading.
  _HEAD_ADC_VALUES: dict[HeadType, int] = {
    "96_d_70": 2745,
    "96_d_200": 2600,
    "384_d_70": 2400,
    "96_f_50": 2200,
    "8_d_lt": 2000,
  }

  def read_head_adc(self) -> int:
    """Return the ADC reading for the configured head type.

    Returns:
      The value from the ADC-to-resistance table for the five head types it
      covers. Any other head type falls back to the 96_d_70 value (2745),
      which is this table's own answer for a head type it has no reading
      for, not a value specific to that head.
    """
    return self._HEAD_ADC_VALUES.get(self._head_type, 2745)

  def detect_smart_head(self) -> bool:
    return True

  def read_smart_head_type(self) -> int:
    return head_type_code(self._head_type)

  # -- Gripper --

  def detect_gripper(self) -> GripperDetectionState:
    return self._gripper_detected

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    self._plate_in_gripper = True
    self._axes["g"].position = position
    logger.debug("Simulation: grip at position %.3f", position)

  def open_gripper(self, position: Optional[float] = None) -> None:
    self._plate_in_gripper = False
    self._axes["g"].position = OPEN_GRIPPER_POSITION if position is None else float(position)
    logger.debug("Simulation: open_gripper")

  def is_plate_in_gripper(self) -> bool:
    return self._plate_in_gripper

  def read_plate_sensor(self, transient: float = 0.0) -> bool:
    return bool(self._plate_sensor_present)

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient: float = 0.0,
  ) -> dict[str, Union[float, bool, None]]:
    self._axes["zg"].position = start_zg
    if self._simulated_scan_height_mm is None:
      self._axes["zg"].position = end_zg
      self._plate_sensor_present = False
      return {
        "detected": False,
        "final_zg": float(end_zg),
      }
    self._plate_sensor_present = True
    detected_zg = float(start_zg) + float(self._simulated_scan_height_mm)
    detected_zg = max(min(detected_zg, max(start_zg, end_zg)), min(start_zg, end_zg))
    self._axes["zg"].position = detected_zg
    return {
      "detected": True,
      "final_zg": float(detected_zg),
      "measured_height_mm": float(self._simulated_scan_height_mm),
    }

  # -- Generic command --

  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    logger.debug("Simulation: send_command(0x%02X) [no-op]", command_id)
    return b""

  # -- Error --

  @property
  def last_error(self) -> Optional[BravoError]:
    return self._last_error

  # -- Simulation-specific --

  def set_head_type(self, head_type: HeadType) -> None:
    """Change the simulated head type.

    Args:
      head_type: The head type to report from now on.
    """
    self._head_type = head_type
    logger.info("Simulation: head type changed to %s", head_type)

  def get_head_type(self) -> HeadType:
    """Return the head type most recently set with :meth:`set_head_type`."""
    return self._head_type

  def set_go_button(self, pressed: bool) -> None:
    """Simulate the Go button being pressed or released.

    Args:
      pressed: The new Go-button state.
    """
    self._go_button_pressed = pressed

  def set_gripper_state(self, detected: GripperDetectionState) -> None:
    """Configure what :meth:`detect_gripper` reports.

    Args:
      detected: The gripper detection state to report from now on.
    """
    self._gripper_detected = detected

  def set_plate_sensor_present(self, present: bool) -> None:
    """Configure what :meth:`read_plate_sensor` reports.

    Args:
      present: Whether the simulated plate sensor should report a plate
        present.
    """
    self._plate_sensor_present = bool(present)

  def set_simulated_scan_height_mm(self, height_mm: Optional[float]) -> None:
    """Configure the stack height :meth:`scan_stack_with_gripper` detects.

    Args:
      height_mm: The simulated distance from the scan's start position to
        the detected stack top, in mm. ``None`` simulates no detection: the
        scan runs to ``end_zg`` without finding anything.
    """
    self._simulated_scan_height_mm = None if height_mm is None else float(height_mm)

  def get_all_positions(self) -> dict[str, float]:
    """Return every axis's position, keyed by its display name (e.g. ``"Zg"``).

    Returns:
      A dict from axis display name to position, in mm (or uL for W).
    """
    return {axis_display_name(axis): ax.position for axis, ax in self._axes.items()}

  def get_all_homed(self) -> dict[Axis, bool]:
    """Return every axis's homed state.

    Returns:
      A dict from axis to whether it currently reports itself homed.
    """
    return {axis: ax.homed for axis, ax in self._axes.items()}

  def get_all_motor_enabled(self) -> dict[Axis, bool]:
    """Return every axis's motor-enabled state.

    Returns:
      A dict from axis to whether its motor drive is currently enabled.
    """
    return {axis: ax.motor_enabled for axis, ax in self._axes.items()}

  def is_motor_enabled(self, axis: Axis) -> bool:
    return self._axes[axis].motor_enabled
