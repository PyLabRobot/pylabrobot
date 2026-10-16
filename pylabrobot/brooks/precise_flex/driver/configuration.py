"""An arm's configuration: what its controller reports, written to a file and read back from one.

`PreciseFlexConfiguration` holds the arm's identity and nests each feature's own configuration. The
driver builds one at setup and holds it, and `None` before.
"""

import dataclasses
import enum
import json
import typing
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from pylabrobot.brooks.precise_flex.kinematics import Axis

from .. import kinematics
from ..kinematics import WorkEnvelope
from .features.arm import PreciseFlexArmConfiguration
from .features.gripper import PreciseFlexGripperConfiguration
from .features.rail import PreciseFlexRailConfiguration

# ---------------------------------------------------------------------------
# Configuration - resolved once at setup
# ---------------------------------------------------------------------------


# -- device configuration --------------------------------------------------


@dataclass(frozen=True)
class PreciseFlexConfiguration:
  """Device configuration resolved once at setup; immutable afterwards.

  The identity fields are read from the controller (`pd <DataID>` and the ``version`` command);
  limits, maxima and kinematics are on ``arm``, ``gripper`` and ``rail``. Derived: ``has_rail`` from
  the joint set, ``is_dual_gripper`` from the axis mask's ``&H80`` bit, ``has_vision_gripper`` from
  the camera count, and ``reach_class`` from the link lengths the controller reports.
  """

  # --- identity / version (DataIDs 100-110, 2002, 116; version command) ---
  manufacturer: str
  controller_model: str
  hardware_version: str
  gpl_version: str
  controller_serial: str
  robot_name: str
  robot_type: int
  tcs_version: str
  modules: tuple
  # --- axes ---
  num_axes: int
  extra_axes: int
  axis_mask: int
  # --- each feature's facts ---
  arm: PreciseFlexArmConfiguration
  gripper: PreciseFlexGripperConfiguration
  rail: Optional[PreciseFlexRailConfiguration] = None
  # --- vision ---
  # How many cameras the controller counts through its vision module; 0 without the module.
  camera_count: int = 0
  # Live state, not a fact; kept for the deprecated `power_state` until it is removed.
  _power_state: Optional[int] = None

  @property
  def has_rail(self) -> bool:
    return self.rail is not None

  @property
  def has_vision_gripper(self) -> bool:
    return self.camera_count > 0

  # -- deprecated: moved to the arm, gripper and rail configurations --

  @property
  def soft_limits(self) -> Dict[Axis, tuple]:
    """Deprecated: merged from the arm, gripper and rail configurations."""
    warnings.warn(
      "`soft_limits` is deprecated, use `arm.soft_limits`, and `soft_limit_range` on "
      "`gripper` and `rail`.",
      DeprecationWarning,
      stacklevel=2,
    )
    merged = {**self.arm.soft_limits, Axis.GRIPPER: self.gripper.soft_limit_range}
    if self.rail is not None:
      merged[Axis.RAIL] = self.rail.soft_limit_range
    return merged

  @property
  def hard_limits(self) -> Dict[Axis, tuple]:
    """Deprecated: merged from the arm, gripper and rail configurations."""
    warnings.warn(
      "`hard_limits` is deprecated, use `arm.hard_limits`, and `hard_limit_range` on "
      "`gripper` and `rail`.",
      DeprecationWarning,
      stacklevel=2,
    )
    merged = {**self.arm.hard_limits, Axis.GRIPPER: self.gripper.hard_limit_range}
    if self.rail is not None and self.rail.hard_limit_range is not None:
      merged[Axis.RAIL] = self.rail.hard_limit_range
    return merged

  @property
  def max_joint_speed(self) -> Dict[Axis, float]:
    """Deprecated: merged from the arm, gripper and rail configurations."""
    warnings.warn(
      "`max_joint_speed` is deprecated, use `arm.max_joint_speed`, and `max_speed` on "
      "`gripper` and `rail`.",
      DeprecationWarning,
      stacklevel=2,
    )
    merged = {**self.arm.max_joint_speed, Axis.GRIPPER: self.gripper.max_speed}
    if self.rail is not None and self.rail.max_speed is not None:
      merged[Axis.RAIL] = self.rail.max_speed
    return merged

  @property
  def max_joint_acceleration(self) -> Dict[Axis, float]:
    """Deprecated: merged from the arm, gripper and rail configurations."""
    warnings.warn(
      "`max_joint_acceleration` is deprecated, use `arm.max_joint_acceleration`, "
      "and `max_acceleration` on `gripper` and `rail`.",
      DeprecationWarning,
      stacklevel=2,
    )
    merged = {**self.arm.max_joint_acceleration, Axis.GRIPPER: self.gripper.max_acceleration}
    if self.rail is not None and self.rail.max_acceleration is not None:
      merged[Axis.RAIL] = self.rail.max_acceleration
    return merged

  @property
  def max_joint_deceleration(self) -> Dict[Axis, float]:
    """Deprecated: merged from the arm, gripper and rail configurations."""
    warnings.warn(
      "`max_joint_deceleration` is deprecated, use `arm.max_joint_deceleration`, "
      "and `max_deceleration` on `gripper` and `rail`.",
      DeprecationWarning,
      stacklevel=2,
    )
    merged = {**self.arm.max_joint_deceleration, Axis.GRIPPER: self.gripper.max_deceleration}
    if self.rail is not None and self.rail.max_deceleration is not None:
      merged[Axis.RAIL] = self.rail.max_deceleration
    return merged

  @property
  def max_cartesian_speed(self) -> float:
    """Deprecated: use ``arm.max_cartesian_speed``."""
    warnings.warn(
      "`max_cartesian_speed` is deprecated, use `arm.max_cartesian_speed`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.max_cartesian_speed

  @property
  def max_cartesian_acceleration(self) -> float:
    """Deprecated: use ``arm.max_cartesian_acceleration``."""
    warnings.warn(
      "`max_cartesian_acceleration` is deprecated, use `arm.max_cartesian_acceleration`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.max_cartesian_acceleration

  @property
  def kinematics(self) -> "kinematics.PF400Params":
    """Deprecated: use ``arm.kinematics``."""
    warnings.warn(
      "`kinematics` is deprecated, use `arm.kinematics`.", DeprecationWarning, stacklevel=2
    )
    return self.arm.kinematics

  @property
  def kinematics_source(self) -> str:
    """Deprecated: use ``arm.kinematics_source``."""
    warnings.warn(
      "`kinematics_source` is deprecated, use `arm.kinematics_source`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.arm.kinematics_source

  @property
  def reach_class(self) -> str:
    """Deprecated: use ``arm.reach_class``."""
    warnings.warn(
      "`reach_class` is deprecated, use `arm.reach_class`.", DeprecationWarning, stacklevel=2
    )
    return self.arm.reach_class

  @property
  def z_range(self) -> tuple:
    """Deprecated: use ``arm.z_range``."""
    warnings.warn("`z_range` is deprecated, use `arm.z_range`.", DeprecationWarning, stacklevel=2)
    return self.arm.z_range

  @property
  def work_envelope(self) -> WorkEnvelope:
    """Deprecated: use ``arm.work_envelope``."""
    warnings.warn(
      "`work_envelope` is deprecated, use `arm.work_envelope`.", DeprecationWarning, stacklevel=2
    )
    return self.arm.work_envelope

  @property
  def gripper_width_range(self) -> tuple:
    """Deprecated: use ``gripper.soft_limit_range``."""
    warnings.warn(
      "`gripper_width_range` is deprecated, use `gripper.soft_limit_range`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.gripper.soft_limit_range

  @property
  def is_dual_gripper(self) -> bool:
    """Deprecated: use ``gripper.is_dual_gripper``."""
    warnings.warn(
      "`is_dual_gripper` is deprecated, use `gripper.is_dual_gripper`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.gripper.is_dual_gripper

  @property
  def power_state(self) -> Optional[int]:
    """Deprecated: use ``PreciseFlexDriver.request_system_state``."""
    warnings.warn(
      "`power_state` is deprecated, use `PreciseFlexDriver.request_system_state`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self._power_state

  @property
  def is_vision_gripper(self) -> bool:
    """Deprecated: use ``has_vision_gripper``."""
    warnings.warn(
      "`is_vision_gripper` is deprecated, use `has_vision_gripper`.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.has_vision_gripper


# -- writing a configuration to a file, and reading it back --------------------------------------


def to_jsonable(value: Any) -> Any:
  """The value as JSON holds it.

  Args:
    value: what to convert - a configuration, or anything one holds.

  Returns:
    The same value in types `json.dump` accepts. Live state, in fields named `_...`, is left out.
  """
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return {
      field.name: to_jsonable(getattr(value, field.name))
      for field in dataclasses.fields(value)
      if not field.name.startswith("_")
    }
  if isinstance(value, enum.Enum):
    return value.name
  if isinstance(value, (list, tuple)):
    return [to_jsonable(item) for item in value]
  if isinstance(value, dict):
    # Keys are written as text because JSON has no other kind. What they were is on the field.
    return {to_jsonable(key): to_jsonable(item) for key, item in value.items()}
  return value


def _restore(hint: Any, value: Any) -> Any:
  """One value, back in the type its field is declared to hold.

  Args:
    hint: the declared type.
    value: the value as JSON held it.

  Returns:
    The value in the declared type.
  """
  if value is None:
    return None
  origin = typing.get_origin(hint)
  args = typing.get_args(hint)
  if origin is Union:  # Optional[X] is Union[X, None]; the None case returned above.
    declared = [arg for arg in args if arg is not type(None)]
    return _restore(declared[0], value) if len(declared) == 1 else value
  if hint is tuple or origin is tuple:
    # A range or a list of modules: JSON holds a list, and the field says it was a tuple.
    return tuple(tuple(item) if isinstance(item, list) else item for item in value)
  if origin is dict:
    key_hint, value_hint = args
    return {_restore(key_hint, key): _restore(value_hint, item) for key, item in value.items()}
  if isinstance(hint, type) and issubclass(hint, enum.Enum):
    return hint[value]
  if dataclasses.is_dataclass(hint) and isinstance(hint, type):
    # Names the class does not have are left out, so a file written by a driver that has since
    # dropped a field still loads.
    field_types = typing.get_type_hints(hint)
    named = {field.name for field in dataclasses.fields(hint)}
    return hint(**{n: _restore(field_types[n], v) for n, v in value.items() if n in named})
  return value


# Each feature's configuration, saved beside the device's own under the feature's name.
DEVICE_FEATURE_CONFIGURATIONS: Dict[str, type] = {
  "arm": PreciseFlexArmConfiguration,
  "gripper": PreciseFlexGripperConfiguration,
  "rail": PreciseFlexRailConfiguration,
}


def read_configuration(path: str) -> Dict[str, Any]:
  """Read a saved configuration back into the dataclasses it was written from.

  Shaped as the device is: the device's own configuration, and each feature's beside it. The
  device's is returned with each feature's nested in it, as setup builds it.

  Args:
    path: a file `PreciseFlexDriver.save_configuration` wrote.

  Returns:
    `{"device": PreciseFlexConfiguration, <name>: configuration}`. Names this driver does not know
    are left out.
  """
  with open(path, encoding="utf-8") as f:
    saved = json.load(f)

  features = {name: saved[name] for name in DEVICE_FEATURE_CONFIGURATIONS if name in saved}
  read: Dict[str, Any] = {
    name: _restore(DEVICE_FEATURE_CONFIGURATIONS[name], value) for name, value in features.items()
  }
  if "device" in saved:
    read["device"] = _restore(PreciseFlexConfiguration, {**saved["device"], **features})
  return read
