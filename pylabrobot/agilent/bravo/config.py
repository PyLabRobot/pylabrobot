"""Typed machine configuration for the Bravo state-machine task layer.

The state-machine tasks (:mod:`.state_machine.tasks`) need a handful of
tuned, per-machine values -- which head is installed, how far above a
labware surface to approach before lowering, the plunger and gripper
mechanical offsets, and the per-axis motion configuration -- bundled into
one typed object so a task receives a single argument instead of several
loosely related ones. :class:`BravoMachineConfig` is that bundle.

Every field here is a tuned physical or operational value, not a computed
default; the numbers come from the same bench measurements and firmware
constants as the rest of this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .axis_config import AxisConfig, default_axis_config
from .types import ALL_AXES, Axis, HeadType


@dataclass
class HeadConfig:
  """Which pipetting head is installed and how its tips are tracked.

  Attributes:
    head_type: The head type installed on the gantry.
    check_on_init: Whether :class:`~.state_machine.tasks.InitializeTask`
      probes the installed head during initialization.
    default_tip_capacity: The nominal capacity, in microlitres, of the tip
      this head normally runs with.
    teach_tip_capacity: The nominal capacity, in microlitres, of the tip
      that was on the head when its teachpoints were taught.
    default_tip_id: The catalogue id of the tip this head normally runs
      with, or ``None`` if unset.
    teach_tip_id: The catalogue id of the tip that was on the head when its
      teachpoints were taught, or ``None`` if unset.
    teach_tip_length_mm: The measured length, in millimetres, of the tip
      that was on the head when its teachpoints were taught, or ``None`` if
      unset.
  """

  head_type: HeadType = "96_d_70"
  check_on_init: bool = True
  default_tip_capacity: float = 200.0
  teach_tip_capacity: float = 200.0
  default_tip_id: Optional[str] = None
  teach_tip_id: Optional[str] = None
  teach_tip_length_mm: Optional[float] = None


@dataclass
class GripperConfig:
  """Mechanical tuning for the plate gripper.

  Attributes:
    grip_current: Current limit, in amps, used when closing on a plate
      body.
    lid_grip_current: Current limit, in amps, used when closing on a plate
      lid.
    y_offset: Offset, in millimetres, between the gripper's Y position and
      the head's Y position for the same deck location.
    gripper_position: The G-axis position, in millimetres, that closes the
      gripper onto a plate.
    pad_zg_reference_mm: With a tip of length
      ``pad_reference_tip_length_mm`` installed, the Zg position at which
      the gripper bottom sits in the plate-pad plane. Paired with
      ``pad_reference_tip_length_mm``; re-measure both together per
      machine rather than assuming the defaults.
    pad_reference_tip_length_mm: The tip length, in millimetres, the
      ``pad_zg_reference_mm`` measurement was taken with.
  """

  grip_current: float = 0.5
  lid_grip_current: float = 0.3
  y_offset: float = 0.0
  gripper_position: float = 5.0
  pad_zg_reference_mm: float = 7.0
  pad_reference_tip_length_mm: float = 26.1


@dataclass
class SafetyConfig:
  """Motion-safety and operational tuning shared across state-machine tasks.

  Attributes:
    ignore_plate_sensor: Whether to skip the gripper's plate-presence
      sensor rather than acting on its reading.
    ignore_w_axis: Whether to skip the W (plunger) axis during
      initialization and homing.
    simulation_mode: Whether the driver is running against a simulated
      instrument rather than real hardware.
    z_safe_position: The Z position, in millimetres, that is clear of every
      labware height on the deck.
    approach_height: Default clearance, in millimetres, to stop above a
      target before the final approach move.
    always_move_to_safe_z: Whether every location move retracts to
      ``z_safe_position`` first, even when the current Z looks clear.
    prompt_home_w: Whether initialization pauses for operator confirmation
      before homing the W axis.
    run_medium_speed: Whether to use the medium speed profile instead of
      fast for general motion.
    enable_tips_off_tip_touch: Whether Tips Off performs a tip-touch
      confirmation move.
    is_srt: Whether this machine is an SRT-generation instrument.
    tips_off_w_position: The W (plunger) target, in microlitres, during
      Tips Off, before returning W to 0.
    tips_off_z_offset: Millimetres the head ejects above the seated press
      depth during Tips Off.
    tips_off_tip_touch_distance: Millimetres of travel for the Tips Off
      tip-touch confirmation move.
    head_tolerance: ADC counts of tolerance allowed when matching a
      resistor-detected head against the expected type.
    safe_location: The deck location number used as a neutral safe
      position.
    prevent_bravo_during_robotic_access: Whether to block Bravo motion
      while an external robot is accessing the deck.
    tip_press_dwell: Seconds to hold the Tips On force press before
      checking whether it is within tolerance.
    plate_sensor_transient: Seconds to allow a plate-presence sensor
      reading to settle before treating it as final.
    allow_tos_fluid_handling: Whether fluid handling is permitted with a
      TOS (tip-on-shaft) tool installed.
    enable_tips_on_tip_touch: Whether Tips On performs a tip-touch
      confirmation move.
    pin_tool_tip_type: The pintool tip type label used for pintool heads.
  """

  ignore_plate_sensor: bool = False
  ignore_w_axis: bool = False
  simulation_mode: bool = False
  z_safe_position: float = 0.0
  approach_height: float = 10.0
  always_move_to_safe_z: bool = True
  prompt_home_w: bool = True
  run_medium_speed: bool = False
  enable_tips_off_tip_touch: bool = True
  is_srt: bool = False
  tips_off_w_position: float = -11.0
  tips_off_z_offset: float = 10.0
  tips_off_tip_touch_distance: float = 314.96
  head_tolerance: int = 25
  safe_location: int = 5
  prevent_bravo_during_robotic_access: bool = True
  tip_press_dwell: float = 0.0
  plate_sensor_transient: float = 0.3
  allow_tos_fluid_handling: bool = False
  enable_tips_on_tip_touch: bool = False
  pin_tool_tip_type: str = "33 mm"


def _default_axes() -> Dict[Axis, AxisConfig]:
  """Build a default :class:`~.axis_config.AxisConfig` for every axis."""
  return {axis: default_axis_config(axis) for axis in ALL_AXES}


@dataclass
class BravoMachineConfig:
  """The tuned, per-machine configuration a state-machine task operates from.

  Bundles the head, gripper, and safety configuration together with the
  per-axis motion configuration, so a task takes one typed argument in
  place of several separately-passed configuration objects.

  Attributes:
    head: The installed head and its tip tracking.
    gripper: Plate-gripper mechanical tuning.
    safety: Motion-safety and operational tuning.
    axes: Per-axis motion configuration, keyed by axis.
    current_limits: Per-head-family override tables for the Tips On force
      press, keyed by table name (``"LT"`` for long-tip heads, ``"ST"``
      for short-tip heads) and then by a string tip count (e.g. ``"96"``)
      to a current limit in amps. ``None``, or a table with no entry
      compatible with the active tip count, falls back to the built-in
      :data:`~.types.LT_TIP_CURRENT_TABLE`/:data:`~.types.ST_TIP_CURRENT_TABLE`.
  """

  head: HeadConfig = field(default_factory=HeadConfig)
  gripper: GripperConfig = field(default_factory=GripperConfig)
  safety: SafetyConfig = field(default_factory=SafetyConfig)
  axes: Dict[Axis, AxisConfig] = field(default_factory=_default_axes)
  current_limits: Optional[Dict[str, Dict[str, float]]] = None
