"""Simulation controller for the Bravo.

A software-only substitute for the Bravo hardware that enables development and AI experimentation without a
physical instrument. All connection/communication methods are no-ops. Positions are tracked in software.
Head detection returns configurable values.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
    AxisMoveInfo,
    BravoController,
    FirmwareVersion,
    JogParams,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import LightCommandData
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.types import (
    Axis,
    DeviceStateFlag,
    GripperDetectionState,
    HeadType,
    NUM_AXES_WITH_GRIPPER,
    OPEN_GRIPPER_POSITION,
    SpeedLevel,
)

logger = logging.getLogger(__name__)


@dataclass
class SimulatedAxis:
    position: float = 0.0
    homed: bool = False
    motor_enabled: bool = False


class SimulationController(BravoController):
    """Software simulation of the Bravo hardware.

    Tracks axis positions in memory. All moves are instantaneous (no timing
    simulation). Useful for testing protocols and AI agent development.
    """

    def __init__(
        self,
        head_type: HeadType = HeadType.HT_96_D_70,
        homing_offsets: dict[Axis, float] | None = None,
    ):
        # Initialise each axis at its homing offset so the coordinate system
        # matches the teachpoint coordinate system from the start.
        offsets = homing_offsets or {}
        self._homing_offsets: dict[Axis, float] = offsets
        self._axes: list[SimulatedAxis] = [
            SimulatedAxis(position=offsets.get(Axis(i), 0.0), homed=True)
            for i in range(NUM_AXES_WITH_GRIPPER)
        ]
        self._connected = False
        self._head_type = head_type
        self._gripper_detected = GripperDetectionState.DETECTED
        self._plate_in_gripper = False
        self._plate_sensor_present = False
        self._simulated_scan_height_mm: float | None = None
        self._last_error: BravoError | None = None
        self._lights: LightCommandData | None = None
        self._go_button_pressed = False
        logger.info("SimulationController created (head_type=%s)", head_type.name)

    # -- Connection --

    def open_serial(self, port: str) -> None:
        logger.info("Simulation: open_serial(%s) [no-op]", port)
        self._connected = True

    def open_tcp(self, address: str) -> None:
        logger.info("Simulation: open_tcp(%s) [no-op]", address)
        self._connected = True

    def close(self) -> None:
        logger.info("Simulation: close()")
        self._connected = False

    def ping(self) -> bool:
        return self._connected

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- Firmware --

    def get_firmware_version(self) -> FirmwareVersion:
        return FirmwareVersion(master="1.2.3", sub1="", sub2="")

    # -- Motion --

    def move(self, moves: list[AxisMoveInfo], wait: bool = True,
             timeout_ms: int = 30000) -> None:
        max_duration = 0.0
        for m in moves:
            ax = self._axes[m.axis]
            old_pos = ax.position
            if m.absolute:
                ax.position = m.position
            else:
                ax.position += m.position
            # Simulate realistic move timing when velocity is specified.
            # This makes W-axis moves (aspirate/dispense/mix) take the
            # correct wall-clock time in simulation, so liquid class
            # velocity parameters produce observable differences.
            if m.velocity > 0 and wait:
                distance = abs(ax.position - old_pos)
                duration = distance / m.velocity
                max_duration = max(max_duration, duration)
            logger.debug(
                "Simulation: move %s to %.3f (abs=%s, vel=%.3f, accel=%.3f)",
                m.axis.label, ax.position, m.absolute,
                m.velocity, m.acceleration,
            )
        if max_duration > 0:
            logger.info(
                "Simulation: waiting %.2fs for move (velocity-based timing)",
                max_duration,
            )
            time.sleep(max_duration)

    def home_axes(self, axes: list[Axis]) -> None:
        for axis in axes:
            self._axes[axis].position = self._homing_offsets.get(axis, 0.0)
            self._axes[axis].homed = True
            logger.debug("Simulation: homed %s", axis.label)

    def jog(self, params: JogParams) -> float:
        ax = self._axes[params.axis]
        ax.position += params.max_position
        logger.debug(
            "Simulation: jog %s to %.3f (max_pos=%.3f)",
            params.axis.label, ax.position, params.max_position,
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
        logger.debug("Simulation: enable_motor(%s)", axis.label)

    def disable_motor(self, axis: Axis) -> None:
        self._axes[axis].motor_enabled = False
        logger.debug("Simulation: disable_motor(%s)", axis.label)

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

    # ADC values for known head types (from the C++ ADC-to-resistance table)
    _HEAD_ADC_VALUES: dict[HeadType, int] = {
        HeadType.HT_96_D_70: 2745,
        HeadType.HT_96_D_200: 2600,
        HeadType.HT_384_D_70: 2400,
        HeadType.HT_96_F_50: 2200,
        HeadType.HT_8_D_LT: 2000,
    }

    def read_head_adc(self) -> int:
        return self._HEAD_ADC_VALUES.get(self._head_type, 2745)

    def detect_smart_head(self) -> bool:
        return True

    def read_smart_head_type(self) -> int:
        return int(self._head_type)

    # -- Gripper --

    def detect_gripper(self) -> GripperDetectionState:
        return self._gripper_detected

    def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
        self._plate_in_gripper = True
        self._axes[Axis.G].position = position
        logger.debug("Simulation: grip at position %.3f", position)

    def open_gripper(self, position: float | None = None) -> None:
        self._plate_in_gripper = False
        self._axes[Axis.G].position = OPEN_GRIPPER_POSITION if position is None else float(position)
        logger.debug("Simulation: open_gripper")

    def is_plate_in_gripper(self) -> bool:
        return self._plate_in_gripper

    def read_plate_sensor(self, transient_ms: int = 0) -> bool:
        return bool(self._plate_sensor_present)

    def scan_stack_with_gripper(
        self,
        *,
        start_zg: float,
        end_zg: float,
        speed: SpeedLevel,
        transient_ms: int = 0,
    ) -> dict[str, float | bool | None]:
        self._axes[Axis.Zg].position = start_zg
        if self._simulated_scan_height_mm is None:
            self._axes[Axis.Zg].position = end_zg
            self._plate_sensor_present = False
            return {
                "detected": False,
                "final_zg": float(end_zg),
            }
        self._plate_sensor_present = True
        detected_zg = float(start_zg) + float(self._simulated_scan_height_mm)
        detected_zg = max(min(detected_zg, max(start_zg, end_zg)), min(start_zg, end_zg))
        self._axes[Axis.Zg].position = detected_zg
        return {
            "detected": True,
            "final_zg": float(detected_zg),
            "measured_height_mm": float(self._simulated_scan_height_mm),
        }

    # -- Generic command --

    def send_command(self, command_id: int, data: bytes = b"",
                     timeout_ms: int = 2000) -> bytes:
        logger.debug("Simulation: send_command(0x%02X) [no-op]", command_id)
        return b""

    # -- Error --

    @property
    def last_error(self) -> BravoError | None:
        return self._last_error

    # -- Simulation-specific --

    def set_head_type(self, head_type: HeadType) -> None:
        """Change the simulated head type."""
        self._head_type = head_type
        logger.info("Simulation: head type changed to %s", head_type.name)

    def set_go_button(self, pressed: bool) -> None:
        """Simulate the Go button being pressed."""
        self._go_button_pressed = pressed

    def set_gripper_state(self, detected: GripperDetectionState) -> None:
        """Configure gripper detection state."""
        self._gripper_detected = detected

    def set_plate_sensor_present(self, present: bool) -> None:
        self._plate_sensor_present = bool(present)

    def set_simulated_scan_height_mm(self, height_mm: float | None) -> None:
        self._simulated_scan_height_mm = None if height_mm is None else float(height_mm)

    def get_all_positions(self) -> dict[Axis, float]:
        """Return all axis positions as a dictionary."""
        return {Axis(i): ax.position for i, ax in enumerate(self._axes)}

    def get_all_homed(self) -> dict[Axis, bool]:
        """Return homed state of all axes."""
        return {Axis(i): ax.homed for i, ax in enumerate(self._axes)}

    def get_all_motor_enabled(self) -> dict[Axis, bool]:
        """Return motor enable state of all axes."""
        return {Axis(i): ax.motor_enabled for i, ax in enumerate(self._axes)}

    def is_motor_enabled(self, axis: Axis) -> bool:
        return self._axes[axis].motor_enabled
