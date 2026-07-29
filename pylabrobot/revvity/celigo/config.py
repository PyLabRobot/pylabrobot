"""Typed configuration for the Celigo USB-IO controller, with an XML loader.

Per-machine hardware configuration is stored as XML under ``<install>/ConfigFiles/``
(e.g. ``USBIOHardwareConfig.config``). Those files are the authoritative, per-instrument
source of truth for axis tuning, galvo/filter-wheel setup, and the analog/digital IO map.

This module mirrors that schema as nested dataclasses. Two ways to obtain a config:

* :meth:`CeligoConfig.from_install` — locate the Celigo ``ConfigFiles`` directory once
  and load the complete per-instrument configuration.
* Construct :class:`CeligoConfig` and its typed subobjects directly — for users who want
  to specify everything in code, or override individual values after loading.

The :class:`~pylabrobot.revvity.celigo.Celigo` constructor accepts a complete
:class:`CeligoConfig`, or loads one with :meth:`CeligoConfig.from_install`.
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_HARDWARE_CONFIG_FILENAME = "USBIOHardwareConfig.config"
_CONFIG_SUBDIRECTORIES = (
  "",
  "ConfigFiles",
  os.path.join("Celigo", "ConfigFiles"),
  os.path.join("Nexcelom Bioscience", "Celigo", "ConfigFiles"),
  os.path.join("Nexcelom", "Celigo", "ConfigFiles"),
  os.path.join("Cyntellect", "Celigo", "ConfigFiles"),
)


def _locate_hardware_config_file(install_dir: str) -> Optional[str]:
  """Locate the one hardware file that establishes the complete config directory."""
  root = install_dir
  if os.path.isfile(root):
    if os.path.basename(root).lower() == _HARDWARE_CONFIG_FILENAME.lower():
      return root
    root = os.path.dirname(root)
  for subdirectory in _CONFIG_SUBDIRECTORIES:
    directory = os.path.join(root, subdirectory)
    exact_path = os.path.join(directory, _HARDWARE_CONFIG_FILENAME)
    if os.path.isfile(exact_path):
      return exact_path
    if not os.path.isdir(directory):
      continue
    case_insensitive_match = next(
      (
        filename
        for filename in os.listdir(directory)
        if filename.lower() == _HARDWARE_CONFIG_FILENAME.lower()
      ),
      None,
    )
    if case_insensitive_match is not None:
      return os.path.join(directory, case_insensitive_match)
  return None


def _xml_local_name(tag: str) -> str:
  """Strip the ``{namespace}`` prefix ElementTree prepends to tags."""
  return tag.rsplit("}", 1)[-1]


def _leaf_scalars(element: ET.Element) -> Dict[str, str]:
  """Map ``localname -> text`` for the direct leaf children of ``element``."""
  out: Dict[str, str] = {}
  for child in element:
    if len(child) == 0 and child.text is not None and child.text.strip():
      out[_xml_local_name(child.tag)] = child.text.strip()
  return out


def _all_leaf_scalars(root: ET.Element) -> Dict[str, str]:
  """Collect every leaf ``localname -> text`` in the document.

  Used for the flat ``<configuration><section><setting>`` DataContract files that hold a
  single object (CalibrationConfig, HardwareDefaultConfig). Last value wins on duplicate
  tag names, which is fine for these single-object files.
  """
  out: Dict[str, str] = {}
  for el in root.iter():
    if len(el) == 0 and el.text is not None and el.text.strip():
      out[_xml_local_name(el.tag)] = el.text.strip()
  return out


class _XmlScalars:
  """Typed, explicit access to one XML object's leaf values."""

  def __init__(self, scalars: Dict[str, str]) -> None:
    self._scalars = scalars
    self._recognized_tags: set[str] = set()

  def text(self, *tags: str) -> str:
    self._recognized_tags.update(tags)
    for tag in tags:
      value = self._scalars.get(tag)
      if value is not None and value.strip():
        return value.strip()
    raise ValueError(f"Configuration is missing required field {tags[0]}")

  def integer(self, *tags: str) -> int:
    # Vendor files sometimes serialize integral settings as ``256.0``.
    value = float(self.text(*tags))
    if not math.isfinite(value) or not value.is_integer():
      raise ValueError(f"Configuration field {tags[0]} must be an integer")
    return int(value)

  def integer_or(self, tag: str, fallback: int) -> int:
    self._recognized_tags.add(tag)
    value = self._scalars.get(tag)
    if value is None:
      return fallback
    parsed = float(value)
    if not math.isfinite(parsed) or not parsed.is_integer():
      raise ValueError(f"Configuration field {tag} must be an integer")
    return int(parsed)

  def floating(self, *tags: str) -> float:
    return float(self.text(*tags))

  def boolean(self, *tags: str) -> bool:
    value = self.text(*tags)
    normalized = value.lower()
    if normalized not in ("true", "false"):
      raise ValueError(f"Invalid boolean value {value!r}")
    return normalized == "true"

  def unrecognized(self) -> Dict[str, str]:
    return {tag: value for tag, value in self._scalars.items() if tag not in self._recognized_tags}


@dataclass(frozen=True)
class _AxisXmlValues:
  motion_name: str
  config_version: int
  motor_type: int
  comm_index: int
  controller_index: int
  axis_index: int
  enabled: bool
  max_velocity: float
  max_acceleration: float
  max_deceleration: float
  max_s_acceleration: int
  moderate_acceleration: float
  minimum_acceleration: float
  moderate_s_acceleration: int
  minimum_s_acceleration: int
  s_curve_support: bool
  home_type: str
  homing_velocity: float
  index_velocity: float
  homing_short_move: int
  home_offset: float
  positive_limit: bool
  negative_limit: bool
  limit_polarity: int
  invert_axis_direction: bool
  default_positive_direction: bool
  moving_current_percentage: int
  holding_current_percentage: int
  loading_current_percentage: int
  moving_overload_limit: int
  mode_enable_limits: bool
  mode_enable_step_and_direction: bool
  mode_enable_position_correction: bool
  mode_enable_motor_slave_to_encoder: bool
  coarse_position_error_window: int
  fine_position_error_window: int
  gain: int
  encoder_to_motor_tick_ratio: float
  backlash_compensation: int
  motor_response_time: int


def _read_axis_values(reader: _XmlScalars, limit_polarity: int) -> _AxisXmlValues:
  return _AxisXmlValues(
    motion_name=reader.text("MotionName"),
    config_version=reader.integer("ConfigVersion"),
    motor_type=reader.integer("MotorType"),
    comm_index=reader.integer("CommIndex"),
    controller_index=reader.integer("ControllerIndex"),
    axis_index=reader.integer("AxisIndex"),
    enabled=reader.boolean("Enabled"),
    max_velocity=reader.floating("MaxVelocity"),
    max_acceleration=reader.floating("MaxAcceleration"),
    max_deceleration=reader.floating("MaxDeceleration"),
    max_s_acceleration=reader.integer("MaxSAcceleration"),
    moderate_acceleration=reader.floating(
      "ModerateAccleration",
      "ModerateAcceleration",
    ),
    minimum_acceleration=reader.floating("MinimumAcceleration"),
    moderate_s_acceleration=reader.integer("ModerateSAcceleration"),
    minimum_s_acceleration=reader.integer("MinimumSAcceleration"),
    s_curve_support=reader.boolean("SCurveSupport"),
    home_type=reader.text("HomeType"),
    homing_velocity=reader.floating("HomingVelocity"),
    index_velocity=reader.floating("IndexVelocity"),
    homing_short_move=reader.integer("HomingShortMove"),
    home_offset=reader.floating("HomeOffset"),
    positive_limit=reader.boolean("PositiveLimit"),
    negative_limit=reader.boolean("NegativeLimit"),
    limit_polarity=limit_polarity,
    invert_axis_direction=reader.boolean("InvertAxisDirection"),
    default_positive_direction=reader.boolean("DefaultPositiveDirection"),
    moving_current_percentage=reader.integer("MovingCurrentPercentage"),
    holding_current_percentage=reader.integer("HoldingCurrentPercentage"),
    loading_current_percentage=reader.integer("LoadingCurrentPercentage"),
    moving_overload_limit=reader.integer("MovingOverloadLimit"),
    mode_enable_limits=reader.boolean("Mode_EnableLimits"),
    mode_enable_step_and_direction=reader.boolean("Mode_EnableStepAndDirection"),
    mode_enable_position_correction=reader.boolean("Mode_EnablePositionCorrection"),
    mode_enable_motor_slave_to_encoder=reader.boolean("Mode_EnableMotorSlaveToEncoder"),
    coarse_position_error_window=reader.integer("CoursePositionErrorWindow"),
    fine_position_error_window=reader.integer("FinePositionErrorWindow"),
    gain=reader.integer("Gain"),
    encoder_to_motor_tick_ratio=reader.floating("EncoderToMotorTickRatio"),
    backlash_compensation=reader.integer("BacklashCompensation"),
    motor_response_time=reader.integer("MotorResponseTime"),
  )


@dataclass
class AxisConfig:
  """Configuration shared by encoder-controlled motors."""

  motion_name: str
  config_version: int
  motor_type: int
  comm_index: int
  controller_index: int
  axis_index: int
  enabled: bool

  # velocity / acceleration profile
  max_velocity: float
  max_acceleration: float
  max_deceleration: float
  max_s_acceleration: int
  moderate_acceleration: float
  minimum_acceleration: float
  moderate_s_acceleration: int
  minimum_s_acceleration: int
  s_curve_support: bool

  # homing
  home_type: str
  homing_velocity: float
  index_velocity: float
  homing_short_move: int
  home_offset: float

  # limits / direction
  positive_limit: bool
  negative_limit: bool
  limit_polarity: int
  invert_axis_direction: bool
  default_positive_direction: bool

  # motor currents (percent)
  moving_current_percentage: int
  holding_current_percentage: int
  loading_current_percentage: int
  moving_overload_limit: int

  # closed-loop / encoder / position correction
  mode_enable_limits: bool
  mode_enable_step_and_direction: bool
  mode_enable_position_correction: bool
  mode_enable_motor_slave_to_encoder: bool
  coarse_position_error_window: int
  fine_position_error_window: int
  gain: int
  encoder_to_motor_tick_ratio: float
  backlash_compensation: int
  motor_response_time: int

  unrecognized_fields: Dict[str, str] = field(default_factory=dict, init=False)

  @classmethod
  def from_element(cls, element: ET.Element) -> "AxisConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    values = _read_axis_values(reader, reader.integer("LimitPolarity"))
    config = cls(
      motion_name=values.motion_name,
      config_version=values.config_version,
      motor_type=values.motor_type,
      comm_index=values.comm_index,
      controller_index=values.controller_index,
      axis_index=values.axis_index,
      enabled=values.enabled,
      max_velocity=values.max_velocity,
      max_acceleration=values.max_acceleration,
      max_deceleration=values.max_deceleration,
      max_s_acceleration=values.max_s_acceleration,
      moderate_acceleration=values.moderate_acceleration,
      minimum_acceleration=values.minimum_acceleration,
      moderate_s_acceleration=values.moderate_s_acceleration,
      minimum_s_acceleration=values.minimum_s_acceleration,
      s_curve_support=values.s_curve_support,
      home_type=values.home_type,
      homing_velocity=values.homing_velocity,
      index_velocity=values.index_velocity,
      homing_short_move=values.homing_short_move,
      home_offset=values.home_offset,
      positive_limit=values.positive_limit,
      negative_limit=values.negative_limit,
      limit_polarity=values.limit_polarity,
      invert_axis_direction=values.invert_axis_direction,
      default_positive_direction=values.default_positive_direction,
      moving_current_percentage=values.moving_current_percentage,
      holding_current_percentage=values.holding_current_percentage,
      loading_current_percentage=values.loading_current_percentage,
      moving_overload_limit=values.moving_overload_limit,
      mode_enable_limits=values.mode_enable_limits,
      mode_enable_step_and_direction=values.mode_enable_step_and_direction,
      mode_enable_position_correction=values.mode_enable_position_correction,
      mode_enable_motor_slave_to_encoder=values.mode_enable_motor_slave_to_encoder,
      coarse_position_error_window=values.coarse_position_error_window,
      fine_position_error_window=values.fine_position_error_window,
      gain=values.gain,
      encoder_to_motor_tick_ratio=values.encoder_to_motor_tick_ratio,
      backlash_compensation=values.backlash_compensation,
      motor_response_time=values.motor_response_time,
    )
    config.unrecognized_fields = reader.unrecognized()
    return config


@dataclass
class LinearAxisConfig(AxisConfig):
  """A linear X, Y, or Z motor with millimeter position bounds."""

  min_position: float
  max_position: float
  mm_per_encoder_tick: float

  @classmethod
  def from_element(cls, element: ET.Element) -> "LinearAxisConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    # Linear-axis vendor files commonly omit LimitPolarity; their fixed polarity is 0.
    values = _read_axis_values(reader, reader.integer_or("LimitPolarity", 0))
    config = cls(
      motion_name=values.motion_name,
      config_version=values.config_version,
      motor_type=values.motor_type,
      comm_index=values.comm_index,
      controller_index=values.controller_index,
      axis_index=values.axis_index,
      enabled=values.enabled,
      max_velocity=values.max_velocity,
      max_acceleration=values.max_acceleration,
      max_deceleration=values.max_deceleration,
      max_s_acceleration=values.max_s_acceleration,
      moderate_acceleration=values.moderate_acceleration,
      minimum_acceleration=values.minimum_acceleration,
      moderate_s_acceleration=values.moderate_s_acceleration,
      minimum_s_acceleration=values.minimum_s_acceleration,
      s_curve_support=values.s_curve_support,
      home_type=values.home_type,
      homing_velocity=values.homing_velocity,
      index_velocity=values.index_velocity,
      homing_short_move=values.homing_short_move,
      home_offset=values.home_offset,
      positive_limit=values.positive_limit,
      negative_limit=values.negative_limit,
      limit_polarity=values.limit_polarity,
      invert_axis_direction=values.invert_axis_direction,
      default_positive_direction=values.default_positive_direction,
      moving_current_percentage=values.moving_current_percentage,
      holding_current_percentage=values.holding_current_percentage,
      loading_current_percentage=values.loading_current_percentage,
      moving_overload_limit=values.moving_overload_limit,
      mode_enable_limits=values.mode_enable_limits,
      mode_enable_step_and_direction=values.mode_enable_step_and_direction,
      mode_enable_position_correction=values.mode_enable_position_correction,
      mode_enable_motor_slave_to_encoder=values.mode_enable_motor_slave_to_encoder,
      coarse_position_error_window=values.coarse_position_error_window,
      fine_position_error_window=values.fine_position_error_window,
      gain=values.gain,
      encoder_to_motor_tick_ratio=values.encoder_to_motor_tick_ratio,
      backlash_compensation=values.backlash_compensation,
      motor_response_time=values.motor_response_time,
      min_position=reader.floating("MinPosition"),
      max_position=reader.floating("MaxPosition"),
      mm_per_encoder_tick=reader.floating("MMPerEncoderTick"),
    )
    config.unrecognized_fields = reader.unrecognized()
    return config


@dataclass
class GalvoConfig:
  """A galvanometer scan axis (``XGalvo`` / ``YGalvo``).

  Note: galvo sections carry no ``MotionName``; they are voltage-driven DAC axes.
  """

  config_version: int
  controller_index: int
  position_error_window: int
  velocity_error_window: int
  big_move_delay: float
  min_voltage: float
  max_voltage: float
  invert_voltage: bool
  enabled: bool
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "GalvoConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      config_version=reader.integer("ConfigVersion"),
      controller_index=reader.integer("ControllerIndex"),
      position_error_window=reader.integer("PositionErrorWindow"),
      velocity_error_window=reader.integer("VelocityErrorWindow"),
      big_move_delay=reader.integer("BigMoveDelayMS") / 1000.0,
      min_voltage=reader.floating("MinVoltage"),
      max_voltage=reader.floating("MaxVoltage"),
      invert_voltage=reader.boolean("InvertVoltage"),
      enabled=reader.boolean("Enabled"),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass(frozen=True)
class GalvoMagnificationCalibration:
  """Imaging center and frame span for one objective magnification."""

  center_voltage: float
  frame_size_volts: float


@dataclass(frozen=True)
class GalvoAxisOpticalCalibration:
  """Optical-center calibration for one galvo axis from LEAP calibration XML."""

  magnifications: Dict[int, GalvoMagnificationCalibration]
  logical_filter_offsets: Dict[int, float]
  laser_center_voltage: float
  uv_laser_center_voltage: float


@dataclass(frozen=True)
class GalvoOpticalCalibration:
  """X/Y imaging-center calibration from ``leaphardwarecalibration.config``."""

  x: GalvoAxisOpticalCalibration
  y: GalvoAxisOpticalCalibration
  source_path: Optional[str] = None


@dataclass
class ExternalCameraControlConfig:
  """Camera trigger/status-line configuration from ``ExternalCameraControl``."""

  config_version: int
  enabled: bool
  invert_busy: bool
  invert_integration: bool
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "ExternalCameraControlConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      config_version=reader.integer("ConfigVersion"),
      enabled=reader.boolean("Enabled"),
      invert_busy=reader.boolean("InvertBusy"),
      invert_integration=reader.boolean("InvertIntegration"),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class FilterMapEntry:
  """One physical<->logical filter position mapping (``FilterMap``)."""

  logical_number: int
  physical_number: int
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "FilterMapEntry":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      logical_number=reader.integer("LogicalNumber"),
      physical_number=reader.integer("PhysicalNumber"),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class FilterWheelConfig(AxisConfig):
  """A discrete rotary filter wheel (``DichroicFilterWheel`` and friends)."""

  encoder_ticks_per_revolution: int
  number_of_filters: int
  filter_map: List[FilterMapEntry]

  @classmethod
  def from_element(cls, element: ET.Element) -> "FilterWheelConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    values = _read_axis_values(reader, reader.integer("LimitPolarity"))
    config = cls(
      motion_name=values.motion_name,
      config_version=values.config_version,
      motor_type=values.motor_type,
      comm_index=values.comm_index,
      controller_index=values.controller_index,
      axis_index=values.axis_index,
      enabled=values.enabled,
      max_velocity=values.max_velocity,
      max_acceleration=values.max_acceleration,
      max_deceleration=values.max_deceleration,
      max_s_acceleration=values.max_s_acceleration,
      moderate_acceleration=values.moderate_acceleration,
      minimum_acceleration=values.minimum_acceleration,
      moderate_s_acceleration=values.moderate_s_acceleration,
      minimum_s_acceleration=values.minimum_s_acceleration,
      s_curve_support=values.s_curve_support,
      home_type=values.home_type,
      homing_velocity=values.homing_velocity,
      index_velocity=values.index_velocity,
      homing_short_move=values.homing_short_move,
      home_offset=values.home_offset,
      positive_limit=values.positive_limit,
      negative_limit=values.negative_limit,
      limit_polarity=values.limit_polarity,
      invert_axis_direction=values.invert_axis_direction,
      default_positive_direction=values.default_positive_direction,
      moving_current_percentage=values.moving_current_percentage,
      holding_current_percentage=values.holding_current_percentage,
      loading_current_percentage=values.loading_current_percentage,
      moving_overload_limit=values.moving_overload_limit,
      mode_enable_limits=values.mode_enable_limits,
      mode_enable_step_and_direction=values.mode_enable_step_and_direction,
      mode_enable_position_correction=values.mode_enable_position_correction,
      mode_enable_motor_slave_to_encoder=values.mode_enable_motor_slave_to_encoder,
      coarse_position_error_window=values.coarse_position_error_window,
      fine_position_error_window=values.fine_position_error_window,
      gain=values.gain,
      encoder_to_motor_tick_ratio=values.encoder_to_motor_tick_ratio,
      backlash_compensation=values.backlash_compensation,
      motor_response_time=values.motor_response_time,
      encoder_ticks_per_revolution=reader.integer("NumberOfEncoderTickPerRev"),
      number_of_filters=reader.integer("NumberOfFilters"),
      filter_map=[
        FilterMapEntry.from_element(child)
        for child in element
        if _xml_local_name(child.tag) == "FilterMap"
      ],
    )
    config.unrecognized_fields = reader.unrecognized()
    return config


@dataclass
class AnalogInputConfig:
  """One analog input from ``IOConfiguration``."""

  config_version: int
  controller_index: int
  channel: int
  enabled: bool
  invert: bool
  io_name: str
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "AnalogInputConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      config_version=reader.integer("ConfigVersion"),
      controller_index=reader.integer("ControllerIndex"),
      channel=reader.integer("Channel"),
      enabled=reader.boolean("Enabled"),
      invert=reader.boolean("Invert"),
      io_name=reader.text("IOName"),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class DigitalIOConfig:
  """One digital input or output from ``IOConfiguration``."""

  config_version: int
  io_type: str
  bit_index: int
  invert: bool
  enabled: bool
  io_name: str
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "DigitalIOConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      config_version=reader.integer("ConfigVersion"),
      io_type=reader.text("IOType"),
      bit_index=reader.integer("BitIndex"),
      invert=reader.boolean("Invert"),
      enabled=reader.boolean("Enabled"),
      io_name=reader.text("IOName"),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class LightingIOConfig:
  """One analog lighting output from ``IOConfiguration``."""

  config_version: int
  controller_index: int
  channel: int
  enabled: bool
  invert: bool
  io_name: str
  min_voltage: float
  max_voltage: float
  delay: float
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "LightingIOConfig":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      config_version=reader.integer("ConfigVersion"),
      controller_index=reader.integer("ControllerIndex"),
      channel=reader.integer("Channel"),
      enabled=reader.boolean("Enabled"),
      invert=reader.boolean("Invert"),
      io_name=reader.text("IOName"),
      min_voltage=reader.floating("MinVoltage"),
      max_voltage=reader.floating("MaxVoltage"),
      delay=reader.integer("DelayMS") / 1000.0,
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class IOConfig:
  """The board IO map: analog ins, digital IOs and lighting IOs."""

  analog_ins: List[AnalogInputConfig]
  digital_ios: List[DigitalIOConfig]
  lighting_ios: List[LightingIOConfig]

  @classmethod
  def from_element(cls, element: ET.Element) -> "IOConfig":
    analog_inputs: List[AnalogInputConfig] = []
    digital_ios: List[DigitalIOConfig] = []
    lighting_ios: List[LightingIOConfig] = []
    for child in element:
      collection_name = _xml_local_name(child.tag)
      if collection_name == "AnalogIns":
        analog_inputs.append(AnalogInputConfig.from_element(child))
      elif collection_name == "DigitalIOs":
        digital_ios.append(DigitalIOConfig.from_element(child))
      elif collection_name == "LightingIOs":
        lighting_ios.append(LightingIOConfig.from_element(child))
    return cls(
      analog_ins=analog_inputs,
      digital_ios=digital_ios,
      lighting_ios=lighting_ios,
    )


@dataclass
class CeligoHardwareConfig:
  """Root hardware config, parsed from ``USBIOHardwareConfig.config``.

  Parse an explicit file with :meth:`from_xml`, or construct it directly as one
  subobject of :class:`CeligoConfig`.
  """

  x_axis: Optional[LinearAxisConfig] = None
  y_axis: Optional[LinearAxisConfig] = None
  z_axis: Optional[LinearAxisConfig] = None
  x_galvo: Optional[GalvoConfig] = None
  y_galvo: Optional[GalvoConfig] = None
  external_camera_control: Optional[ExternalCameraControlConfig] = None
  beam_expander: Optional[AxisConfig] = None
  camera_filter_wheel: Optional[FilterWheelConfig] = None
  dichroic_filter_wheel: Optional[FilterWheelConfig] = None
  door: Optional[AxisConfig] = None
  excitation_filter_wheel: Optional[FilterWheelConfig] = None
  excitation_nd_filter_wheel: Optional[FilterWheelConfig] = None
  laser_attenuator: Optional[AxisConfig] = None
  laser_nd_filter_wheel: Optional[FilterWheelConfig] = None
  magnification_changer: Optional[FilterWheelConfig] = None
  io: Optional[IOConfig] = None
  source_path: Optional[str] = None

  @staticmethod
  def _inner_root(tree_root: ET.Element) -> ET.Element:
    """Descend through the .NET ``xmlSerializerSection`` envelope to the config body.

    Returns the element whose children are ``XAxis``/``YAxis``/... (i.e. the serialized
    ``USBIOConfigurationFile``). Tolerates the file being given with or without the
    ``<configuration>`` / ``<xmlSerializerSection>`` wrapper.
    """
    for el in tree_root.iter():
      child_tags = {_xml_local_name(c.tag) for c in el}
      if "XAxis" in child_tags or "YAxis" in child_tags:
        return el
    return tree_root

  @classmethod
  def from_xml(cls, path: str) -> "CeligoHardwareConfig":
    """Parse a ``USBIOHardwareConfig.config`` file into a config object."""
    root = ET.parse(path).getroot()
    body = cls._inner_root(root)
    hardware_config = cls(source_path=os.path.abspath(path))
    for child in body:
      name = _xml_local_name(child.tag)
      if name == "XAxis":
        hardware_config.x_axis = LinearAxisConfig.from_element(child)
      elif name == "YAxis":
        hardware_config.y_axis = LinearAxisConfig.from_element(child)
      elif name == "ZSingleAxis":
        hardware_config.z_axis = LinearAxisConfig.from_element(child)
      elif name == "XGalvo":
        hardware_config.x_galvo = GalvoConfig.from_element(child)
      elif name == "YGalvo":
        hardware_config.y_galvo = GalvoConfig.from_element(child)
      elif name == "ExternalCameraControl":
        hardware_config.external_camera_control = ExternalCameraControlConfig.from_element(child)
      elif name == "BeamExpander":
        hardware_config.beam_expander = AxisConfig.from_element(child)
      elif name == "CameraFilterWheel":
        hardware_config.camera_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "DichroicFilterWheel":
        hardware_config.dichroic_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "Door":
        hardware_config.door = AxisConfig.from_element(child)
      elif name == "ExcitationFilterWheel":
        hardware_config.excitation_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "ExcitationNDFilterWheel":
        hardware_config.excitation_nd_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "LaserAttenuator":
        hardware_config.laser_attenuator = AxisConfig.from_element(child)
      elif name == "LaserNDFilterWheel":
        hardware_config.laser_nd_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "MagChanger":
        hardware_config.magnification_changer = FilterWheelConfig.from_element(child)
      elif name == "IOConfiguration":
        hardware_config.io = IOConfig.from_element(child)
    return hardware_config


@dataclass
class ChannelDescriptor:
  """An imaging channel from ``ChannelConfig.xml`` (Default / HWAF / fluorescence)."""

  name: str
  fixed_type: str
  description: str
  guid: str
  calibration_index: int
  channel_key: str
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_element(cls, element: ET.Element) -> "ChannelDescriptor":
    reader = _XmlScalars(_leaf_scalars(element))
    return cls(
      name=reader.text("Name"),
      fixed_type=reader.text("FixedType"),
      description=reader.text("Description"),
      guid=reader.text("ChannelDescGUID"),
      calibration_index=reader.integer("CalibrationIndex"),
      channel_key=reader.text("GetChanDescKey"),
      unrecognized_fields=reader.unrecognized(),
    )


def load_channel_descriptors(path: str) -> List[ChannelDescriptor]:
  """Parse ``ChannelConfig.xml`` into a list of :class:`ChannelDescriptor`.

  Collects channel descriptors from the XML, deduplicating by GUID and preserving order.
  """
  root = ET.parse(path).getroot()
  channels: List[ChannelDescriptor] = []
  seen: set = set()
  for el in root.iter():
    if _xml_local_name(el.tag) == "ChannelDescriptor":
      ch = ChannelDescriptor.from_element(el)
      if ch.guid and ch.guid in seen:
        continue
      seen.add(ch.guid)
      channels.append(ch)
  return channels


@dataclass(frozen=True)
class IlluminationChannelConfig:
  """Hardware recipe for one image channel from ``leaphardwarecalibration.config``."""

  name: str
  display_name: str
  logical_filter: int
  bit_value: Optional[int]
  intensity_percent: float
  lighting_io_name: str
  strobe: bool
  z_offset_to_brightfield_mm: float
  mm_per_pixel_x_correction_to_brightfield: float
  mm_per_pixel_y_correction_to_brightfield: float


def _magnification_voltage_tag(magnification: int) -> str:
  if magnification not in (3, 5, 10, 20):
    raise ValueError("magnification must be one of 3, 5, 10, or 20")
  return f"VoltageMag{magnification}X"


def _normalize_illumination_channel_name(display_name: str) -> str:
  normalized = display_name.strip().lower().replace("-", " ")
  if normalized.startswith("brightfield"):
    return "brightfield"
  if normalized.startswith("far red"):
    return "far_red"
  for name in ("green", "red", "blue"):
    if normalized.startswith(name):
      return name
  return normalized.replace(" ", "_")


def _load_illumination_channels(
  root: ET.Element,
  magnification: int,
) -> Dict[str, IlluminationChannelConfig]:
  voltage_tag = _magnification_voltage_tag(magnification)
  channels: Dict[str, IlluminationChannelConfig] = {}

  for element in root.iter():
    element_name = _xml_local_name(element.tag)
    if element_name not in ("BFVoltageCal", "FLLight"):
      continue
    scalars = _all_leaf_scalars(element)
    display_name = "Brightfield" if element_name == "BFVoltageCal" else scalars.get("Name", "")
    name = _normalize_illumination_channel_name(display_name)
    if not name:
      continue
    required = {"LogicalFilter", voltage_tag}
    if element_name == "FLLight":
      required.update({"Name", "BitValue"})
    missing = sorted(required - scalars.keys())
    if missing:
      raise ValueError(f"Channel {display_name or element_name} is missing {', '.join(missing)}")
    logical_filter = int(scalars["LogicalFilter"])
    intensity = float(scalars[voltage_tag])
    bit_value = None if element_name == "BFVoltageCal" else int(scalars["BitValue"])
    z_offset = float(scalars.get("CalibratedZOffsetToBFMM", "0"))
    x_correction = float(scalars.get("CalibratedMMPerPixelXCorrectionToBF", "1"))
    y_correction = float(scalars.get("CalibratedMMPerPixelYCorrectionToBF", "1"))
    if logical_filter < 0 or not math.isfinite(intensity) or not 0 <= intensity <= 100:
      raise ValueError(f"Channel {display_name} has invalid filter/intensity calibration")
    if bit_value is not None and not 0 <= bit_value <= 3:
      raise ValueError(f"Channel {display_name} has invalid selector BitValue {bit_value}")
    if not math.isfinite(z_offset) or any(
      not math.isfinite(value) or value <= 0 for value in (x_correction, y_correction)
    ):
      raise ValueError(f"Channel {display_name} has invalid spatial calibration")
    channels[name] = IlluminationChannelConfig(
      name=name,
      display_name=display_name,
      logical_filter=logical_filter,
      bit_value=bit_value,
      intensity_percent=intensity,
      lighting_io_name=(
        "eBrightFieldIntensity" if element_name == "BFVoltageCal" else "eFluorescentIntensity"
      ),
      strobe=element_name == "FLLight",
      z_offset_to_brightfield_mm=z_offset,
      mm_per_pixel_x_correction_to_brightfield=x_correction,
      mm_per_pixel_y_correction_to_brightfield=y_correction,
    )
  return channels


def load_illumination_channels(
  path: str, magnification: int = 10
) -> Dict[str, IlluminationChannelConfig]:
  """Load one magnification's illumination recipes from LEAP calibration XML."""
  return _load_illumination_channels(ET.parse(path).getroot(), magnification)


def _load_all_illumination_channels(
  root: ET.Element,
) -> Dict[int, Dict[str, IlluminationChannelConfig]]:
  magnifications = {
    int(tag[len("VoltageMag") : -1])
    for element in root.iter()
    for tag in (_xml_local_name(element.tag),)
    if tag.startswith("VoltageMag") and tag.endswith("X") and tag[len("VoltageMag") : -1].isdigit()
  }
  if not magnifications:
    raise ValueError("LEAP calibration contains no magnification-specific channel voltages")
  return {
    magnification: _load_illumination_channels(root, magnification)
    for magnification in sorted(magnifications)
  }


def _load_galvo_axis_optical_calibration(element: ET.Element) -> GalvoAxisOpticalCalibration:
  magnifications: Dict[int, GalvoMagnificationCalibration] = {}
  logical_filter_offsets: Dict[int, float] = {}
  laser_center_voltage: Optional[float] = None
  uv_laser_center_voltage: Optional[float] = None
  for child in element:
    name = _xml_local_name(child.tag)
    if name.startswith("ImageCenter") and name.endswith("X"):
      try:
        magnification = int(name[len("ImageCenter") : -1])
      except ValueError:
        continue
      values = _all_leaf_scalars(child)
      if "CenterVoltage" not in values or "FrameSizeVolts" not in values:
        raise ValueError(f"{name} is missing center/frame calibration")
      magnifications[magnification] = GalvoMagnificationCalibration(
        center_voltage=float(values["CenterVoltage"]),
        frame_size_volts=float(values["FrameSizeVolts"]),
      )
    elif name == "LogicalFilterCenterVoltageOffset":
      values = _all_leaf_scalars(child)
      if "LogicalNumber" in values and "CenterVoltageOffset" in values:
        logical_filter_offsets[int(values["LogicalNumber"])] = float(values["CenterVoltageOffset"])
    elif name == "LaserCenterVoltage" and child.text:
      laser_center_voltage = float(child.text)
    elif name == "UVLaserCenterVoltage" and child.text:
      uv_laser_center_voltage = float(child.text)
  if laser_center_voltage is None or uv_laser_center_voltage is None:
    missing_centers = [
      name
      for name, value in (
        ("LaserCenterVoltage", laser_center_voltage),
        ("UVLaserCenterVoltage", uv_laser_center_voltage),
      )
      if value is None
    ]
    raise ValueError(f"{_xml_local_name(element.tag)} is missing {', '.join(missing_centers)}")
  return GalvoAxisOpticalCalibration(
    magnifications=magnifications,
    logical_filter_offsets=logical_filter_offsets,
    laser_center_voltage=laser_center_voltage,
    uv_laser_center_voltage=uv_laser_center_voltage,
  )


def _load_galvo_optical_calibration(
  root: ET.Element,
  source_path: Optional[str] = None,
) -> GalvoOpticalCalibration:
  axes: Dict[str, GalvoAxisOpticalCalibration] = {}
  for element in root.iter():
    name = _xml_local_name(element.tag)
    if name in ("XGalvo", "YGalvo"):
      axes[name] = _load_galvo_axis_optical_calibration(element)
  missing = [name for name in ("XGalvo", "YGalvo") if name not in axes]
  if missing:
    raise ValueError(f"Missing galvo optical calibration section(s): {', '.join(missing)}")
  for name, axis in axes.items():
    if not axis.magnifications:
      raise ValueError(f"{name} has no imaging-center calibration")
    for magnification, values in axis.magnifications.items():
      if (
        not math.isfinite(values.center_voltage)
        or not math.isfinite(values.frame_size_volts)
        or values.frame_size_volts <= 0
      ):
        raise ValueError(f"{name} {magnification}X calibration is invalid")
    if any(
      not math.isfinite(value)
      for value in (
        *axis.logical_filter_offsets.values(),
        axis.laser_center_voltage,
        axis.uv_laser_center_voltage,
      )
    ):
      raise ValueError(f"{name} contains a non-finite center calibration")
  return GalvoOpticalCalibration(
    x=axes["XGalvo"],
    y=axes["YGalvo"],
    source_path=os.path.abspath(source_path) if source_path is not None else None,
  )


def load_galvo_optical_calibration(path: str) -> GalvoOpticalCalibration:
  """Load galvo centers, frame spans, and filter offsets from LEAP calibration XML."""
  return _load_galvo_optical_calibration(ET.parse(path).getroot(), source_path=path)


@dataclass
class Calibrated2DPolynomialTransform:
  """A 2D quadratic or cubic coordinate transform.

  Each named polynomial term maps to an ``(x_coeff, y_coeff)`` pair. ``forward`` and
  ``reverse`` hold the two directions (galvo volts->mm and mm->galvo volts).
  """

  forward: Dict[str, "tuple[float, float]"]
  reverse: Dict[str, "tuple[float, float]"]
  order: int
  successful: Optional[bool] = None
  source_path: Optional[str] = None

  @staticmethod
  def _terms(direction_element: ET.Element) -> Dict[str, "tuple[float, float]"]:
    terms: Dict[str, "tuple[float, float]"] = {}
    for term in direction_element:
      name = _xml_local_name(term.tag)
      x = y = 0.0
      for comp in term:
        cn = _xml_local_name(comp.tag)
        if cn == "X":
          x = float(comp.text) if comp.text else 0.0
        elif cn == "Y":
          y = float(comp.text) if comp.text else 0.0
      terms[name] = (x, y)
    return terms

  @classmethod
  def from_element(cls, element: ET.Element) -> "Calibrated2DPolynomialTransform":
    type_name = _xml_local_name(element.tag).lower()
    forward: Optional[Dict[str, "tuple[float, float]"]] = None
    reverse: Optional[Dict[str, "tuple[float, float]"]] = None
    successful: Optional[bool] = None
    for el in element:
      name = _xml_local_name(el.tag)
      if name == "Forward":
        forward = cls._terms(el)
      elif name == "Reverse":
        reverse = cls._terms(el)
      elif name == "LastGalvoCalSuccessful" and el.text:
        successful = _XmlScalars({"value": el.text}).boolean("value")
    if forward is None or reverse is None:
      raise ValueError("Galvo transformation requires Forward and Reverse coefficients")
    return cls(
      forward=forward,
      reverse=reverse,
      order=2 if "quadratic" in type_name else 3,
      successful=successful,
    )

  @classmethod
  def from_xml(cls, path: str) -> "Calibrated2DPolynomialTransform":
    root = ET.parse(path).getroot()
    transform = next(
      (
        el
        for el in root.iter()
        if "transformation" in _xml_local_name(el.tag).lower()
        or "tranformation" in _xml_local_name(el.tag).lower()
      ),
      root,
    )
    obj = cls.from_element(transform)
    obj.source_path = os.path.abspath(path)
    return obj


def load_galvo_calibrations(path: str) -> Dict[int, Calibrated2DPolynomialTransform]:
  """Load every per-logical-filter galvo transform in a calibration file."""
  root = ET.parse(path).getroot()
  calibrations: Dict[int, Calibrated2DPolynomialTransform] = {}
  for setting in root.iter():
    if _xml_local_name(setting.tag) != "setting":
      continue
    key = setting.attrib.get("key", "")
    prefix = "GalvoCalibrationConfig_"
    if not key.startswith(prefix):
      continue
    try:
      logical_filter = int(key[len(prefix) :])
    except ValueError:
      continue
    transform_el = next(iter(setting), None)
    if transform_el is None:
      continue
    transform = Calibrated2DPolynomialTransform.from_element(transform_el)
    transform.source_path = os.path.abspath(path)
    calibrations[logical_filter] = transform
  return calibrations


@dataclass
class CalibrationConfig:
  """Per-machine optical/stage calibration (``CalibrationConfig.xml``).

  Feeds the pixel<->mm and sample-mm<->stage-mm affine transforms (see
  :mod:`pylabrobot.revvity.celigo.coordinates`).
  """

  microns_per_pixel_x: float
  microns_per_pixel_y: float
  image_width_pixels: int
  image_height_pixels: int
  image_to_stage_theta_radians: float
  galvo_to_stage_theta_radians: float
  calibrated_plate_corner_x: float
  calibrated_plate_corner_y: float
  calibrated_plate_to_stage_theta_radians: float
  stage_x_scale: float
  stage_y_scale: float
  stage_shear: float
  stage_x_shear_offset: float
  stage_y_shear_offset: float
  calibrated_z_position: float
  calibrated_z_glass_plate_delta: float
  z_plane_x_coeff: float
  z_plane_y_coeff: float
  source_path: Optional[str] = None
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_xml(cls, path: str) -> "CalibrationConfig":
    reader = _XmlScalars(_all_leaf_scalars(ET.parse(path).getroot()))
    return cls(
      microns_per_pixel_x=reader.floating("MicronsPerPixelX"),
      microns_per_pixel_y=reader.floating("MicronsPerPixelY"),
      image_width_pixels=reader.integer("ImageWidthPixels"),
      image_height_pixels=reader.integer("ImageHeightPixels"),
      image_to_stage_theta_radians=reader.floating("ImageToStageThetaRadians"),
      galvo_to_stage_theta_radians=reader.floating("GalvoToStageThetaRadians"),
      calibrated_plate_corner_x=reader.floating("CalibratedPlateCornerX"),
      calibrated_plate_corner_y=reader.floating("CalibratedPlateCornerY"),
      calibrated_plate_to_stage_theta_radians=reader.floating("CalibratedPlateToStageThetaRadians"),
      stage_x_scale=reader.floating("StageXScale"),
      stage_y_scale=reader.floating("StageYScale"),
      stage_shear=reader.floating("StageShear"),
      stage_x_shear_offset=reader.floating("StageXShearOffset"),
      stage_y_shear_offset=reader.floating("StageYShearOffset"),
      calibrated_z_position=reader.floating("CalibratedZPosition"),
      calibrated_z_glass_plate_delta=reader.floating("CalibratedZGlassPlateDelta"),
      z_plane_x_coeff=reader.floating("ZPlaneXCoeff"),
      z_plane_y_coeff=reader.floating("ZPlaneYCoeff"),
      source_path=os.path.abspath(path),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class HardwareDefaultConfig:
  """Instrument defaults (``HardwareDefaultConfig.xml``): plate corner, FOV, galvo MM/V."""

  default_calibrated_z: float
  default_plate_x_corner_stage_coordinate: float
  default_plate_y_corner_stage_coordinate: float
  default_x_field_of_view_mm: float
  default_y_field_of_view_mm: float
  default_x_galvo_mm_per_volt: float
  default_y_galvo_mm_per_volt: float
  source_path: Optional[str] = None
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_xml(cls, path: str) -> "HardwareDefaultConfig":
    reader = _XmlScalars(_all_leaf_scalars(ET.parse(path).getroot()))
    return cls(
      default_calibrated_z=reader.floating("DefaultCalibratedZ"),
      default_plate_x_corner_stage_coordinate=reader.floating("DefaultPlateXCornerStageCoordinate"),
      default_plate_y_corner_stage_coordinate=reader.floating("DefaultPlateYCornerStageCoordinate"),
      default_x_field_of_view_mm=reader.floating("DefaultXFieldOfViewMM"),
      default_y_field_of_view_mm=reader.floating("DefaultYFieldOfViewMM"),
      default_x_galvo_mm_per_volt=reader.floating("DefaultXGalvoMMPerVolt"),
      default_y_galvo_mm_per_volt=reader.floating("DefaultYGalvoMMPerVolt"),
      source_path=os.path.abspath(path),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class NavigationConfig:
  """Galvo reach and frame overlap from ``NavigationConfig.xml``."""

  frame_overlap_x_mm: float
  frame_overlap_y_mm: float
  max_galvo_deflection_x_mm: float
  max_galvo_deflection_y_mm: float
  source_path: Optional[str] = None
  unrecognized_fields: Dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_xml(cls, path: str) -> "NavigationConfig":
    reader = _XmlScalars(_all_leaf_scalars(ET.parse(path).getroot()))
    return cls(
      frame_overlap_x_mm=reader.floating("FrameOverlapXMM"),
      frame_overlap_y_mm=reader.floating("FrameOverlapYMM"),
      max_galvo_deflection_x_mm=reader.floating("MaxGalvoDeflectionXMM"),
      max_galvo_deflection_y_mm=reader.floating("MaxGalvoDeflectionYMM"),
      source_path=os.path.abspath(path),
      unrecognized_fields=reader.unrecognized(),
    )


@dataclass
class CeligoConfig:
  """All parsed configuration for one Celigo instrument.

  :meth:`from_install` locates the hardware file once, indexes its directory once, and
  loads every required companion configuration into explicit subobjects. Illumination
  recipes for every magnification present in the vendor file are loaded up front.
  """

  hardware: CeligoHardwareConfig
  channel_descriptors: List[ChannelDescriptor]
  channels_by_magnification: Dict[int, Dict[str, IlluminationChannelConfig]]
  calibration: CalibrationConfig
  hardware_defaults: HardwareDefaultConfig
  galvo_calibrations: Dict[int, Calibrated2DPolynomialTransform]
  galvo_optical_calibration: GalvoOpticalCalibration
  navigation: NavigationConfig
  magnification: int = 3

  def __post_init__(self) -> None:
    if self.magnification not in (3, 5, 10, 20):
      raise ValueError("magnification must be one of 3, 5, 10, or 20")
    if self.magnification not in self.channels_by_magnification:
      raise ValueError(f"No illumination-channel calibration is loaded for {self.magnification}X")

  @property
  def channels(self) -> Dict[str, IlluminationChannelConfig]:
    """Illumination recipes for the active magnification."""
    try:
      return self.channels_by_magnification[self.magnification]
    except KeyError as exc:
      raise ValueError(
        f"No illumination-channel calibration is loaded for {self.magnification}X"
      ) from exc

  @classmethod
  def from_install(
    cls,
    install_dir: str,
    magnification: int = 3,
  ) -> "CeligoConfig":
    """Load the complete configuration set used to initialize :class:`Celigo`.

    ``install_dir`` may be the Celigo installation root, its ``ConfigFiles`` directory,
    or the path to ``USBIOHardwareConfig.config``.

    .. code-block:: python

       config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
       celigo = Celigo(config=config)
    """
    if magnification not in (3, 5, 10, 20):
      raise ValueError("magnification must be one of 3, 5, 10, or 20")
    hardware_path = _locate_hardware_config_file(install_dir)
    if hardware_path is None:
      raise FileNotFoundError(
        f"Could not find {_HARDWARE_CONFIG_FILENAME}. Pass install_dir= pointing "
        "at the Celigo install root or its ConfigFiles directory."
      )
    config_directory = os.path.dirname(os.path.abspath(hardware_path))
    files_by_name = {
      filename.lower(): os.path.join(config_directory, filename)
      for filename in os.listdir(config_directory)
      if os.path.isfile(os.path.join(config_directory, filename))
    }

    def require_companion_file(filename: str) -> str:
      path = files_by_name.get(filename.lower())
      if path is None:
        raise FileNotFoundError(
          f"Required Celigo configuration file {filename} is missing from {config_directory}"
        )
      return path

    illumination_calibration_path = require_companion_file("leaphardwarecalibration.config")
    channel_config_path = require_companion_file("ChannelConfig.xml")
    calibration_path = require_companion_file("CalibrationConfig.xml")
    hardware_defaults_path = require_companion_file("HardwareDefaultConfig.xml")
    galvo_calibration_path = require_companion_file("GalvoCalibrationConfig.xml")
    navigation_path = require_companion_file("NavigationConfig.xml")
    illumination_root = ET.parse(illumination_calibration_path).getroot()

    return cls(
      hardware=CeligoHardwareConfig.from_xml(hardware_path),
      channel_descriptors=load_channel_descriptors(channel_config_path),
      channels_by_magnification=_load_all_illumination_channels(illumination_root),
      calibration=CalibrationConfig.from_xml(calibration_path),
      hardware_defaults=HardwareDefaultConfig.from_xml(hardware_defaults_path),
      galvo_calibrations=load_galvo_calibrations(galvo_calibration_path),
      galvo_optical_calibration=_load_galvo_optical_calibration(
        illumination_root,
        source_path=illumination_calibration_path,
      ),
      navigation=NavigationConfig.from_xml(navigation_path),
      magnification=magnification,
    )
