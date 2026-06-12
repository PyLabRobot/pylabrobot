"""Typed configuration for the Celigo USB-IO controller, with an XML loader.

Per-machine hardware configuration is stored as XML under ``<install>/ConfigFiles/``
(e.g. ``USBIOHardwareConfig.config``). Those files are the authoritative, per-instrument
source of truth for axis tuning, galvo/filter-wheel setup, and the analog/digital IO map.

This module mirrors that schema as nested dataclasses. Two ways to obtain a config:

* :meth:`CeligoHardwareConfig.from_install` — the default: locate and parse the Celigo
  ``ConfigFiles`` directory. Returns a fully-populated config object.
* Construct :class:`CeligoHardwareConfig` (and its members) directly — for users who
  want to specify everything in code, or override individual values after loading.

The backend accepts ``config: Optional[CeligoHardwareConfig]`` and falls back to
:meth:`~CeligoHardwareConfig.from_install` when ``None`` is given.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Dict, List, Optional, TypeVar

_T = TypeVar("_T", bound="_FromXmlMixin")


def _localname(tag: str) -> str:
  """Strip the ``{namespace}`` prefix ElementTree prepends to tags."""
  return tag.rsplit("}", 1)[-1]


def _coerce(text: Optional[str], typ: type) -> Any:
  if text is None:
    return None
  text = text.strip()
  if typ is bool:
    return text.lower() == "true"
  if typ is int:
    # tolerate values written as floats ("256.0")
    return int(float(text)) if text else 0
  if typ is float:
    return float(text) if text else 0.0
  return text


def _leaf_scalars(element: ET.Element) -> Dict[str, str]:
  """Map ``localname -> text`` for the direct leaf children of ``element``."""
  out: Dict[str, str] = {}
  for child in element:
    if len(child) == 0 and child.text is not None and child.text.strip():
      out[_localname(child.tag)] = child.text.strip()
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
      out[_localname(el.tag)] = el.text.strip()
  return out


class _FromXmlMixin:
  """Build a dataclass from an XML element using a ``{XmlTag: (attr, type)}`` map."""

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]]

  @classmethod
  def from_element(cls: type[_T], element: ET.Element) -> _T:
    return cls.from_scalars(_leaf_scalars(element))

  @classmethod
  def from_scalars(cls: type[_T], scalars: Dict[str, str]) -> _T:
    kwargs: Dict[str, Any] = {}
    known_attrs = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    extra: Dict[str, str] = {}
    for tag, value in scalars.items():
      mapping = cls._FIELD_MAP.get(tag)
      if mapping is None:
        extra[tag] = value
        continue
      attr, typ = mapping
      kwargs[attr] = _coerce(value, typ)
    if "extra" in known_attrs:
      kwargs["extra"] = extra
    return cls(**kwargs)  # type: ignore[call-arg]


@dataclass
class AxisConfig(_FromXmlMixin):
  """A single motion axis (``SingleAxisConfigBase`` — X, Y, or Z stage motor)."""

  motion_name: str = ""
  config_version: int = 0
  motor_type: int = 0
  comm_index: int = 0
  controller_index: int = 0
  axis_index: int = 0
  enabled: bool = True

  # velocity / acceleration profile
  max_velocity: float = 0.0
  max_acceleration: float = 0.0
  max_deceleration: float = 0.0
  max_s_acceleration: int = 0
  moderate_acceleration: float = 0.0
  minimum_acceleration: float = 0.0
  moderate_s_acceleration: int = 0
  minimum_s_acceleration: int = 0
  s_curve_support: bool = False

  # homing
  home_type: str = ""
  homing_velocity: float = 0.0
  index_velocity: float = 0.0
  homing_short_move: int = 0
  home_offset: float = 0.0

  # limits / direction
  positive_limit: bool = False
  negative_limit: bool = False
  limit_polarity: int = 0
  invert_axis_direction: bool = False
  default_positive_direction: bool = False
  min_position: float = 0.0
  max_position: float = 0.0

  # motor currents (percent)
  moving_current_percentage: int = 0
  holding_current_percentage: int = 0
  loading_current_percentage: int = 0
  maximum_allowed_current_percentage: int = 0
  moving_overload_limit: int = 0

  # closed-loop / encoder / position correction
  mode_enable_limits: bool = False
  mode_enable_step_and_direction: bool = False
  mode_enable_position_correction: bool = False
  mode_enable_motor_slave_to_encoder: bool = False
  course_position_error_window: int = 0
  fine_position_error_window: int = 0
  gain: int = 0
  encoder_to_motor_tick_ratio: float = 0.0
  backlash_compensation: int = 0
  motor_response_time: int = 0
  mm_per_encoder_tick: float = 0.0
  number_of_encoder_tick_per_rev: int = 0

  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "MotionName": ("motion_name", str),
    "ConfigVersion": ("config_version", int),
    "MotorType": ("motor_type", int),
    "CommIndex": ("comm_index", int),
    "ControllerIndex": ("controller_index", int),
    "AxisIndex": ("axis_index", int),
    "Enabled": ("enabled", bool),
    "MaxVelocity": ("max_velocity", float),
    "MaxAcceleration": ("max_acceleration", float),
    "MaxDeceleration": ("max_deceleration", float),
    "MaxSAcceleration": ("max_s_acceleration", int),
    "ModerateAccleration": ("moderate_acceleration", float),  # (XML tag is spelled this way)
    "ModerateAcceleration": ("moderate_acceleration", float),
    "MinimumAcceleration": ("minimum_acceleration", float),
    "ModerateSAcceleration": ("moderate_s_acceleration", int),
    "MinimumSAcceleration": ("minimum_s_acceleration", int),
    "SCurveSupport": ("s_curve_support", bool),
    "HomeType": ("home_type", str),
    "HomingVelocity": ("homing_velocity", float),
    "IndexVelocity": ("index_velocity", float),
    "HomingShortMove": ("homing_short_move", int),
    "HomeOffset": ("home_offset", float),
    "PositiveLimit": ("positive_limit", bool),
    "NegativeLimit": ("negative_limit", bool),
    "LimitPolarity": ("limit_polarity", int),
    "InvertAxisDirection": ("invert_axis_direction", bool),
    "DefaultPositiveDirection": ("default_positive_direction", bool),
    "MinPosition": ("min_position", float),
    "MaxPosition": ("max_position", float),
    "MovingCurrentPercentage": ("moving_current_percentage", int),
    "HoldingCurrentPercentage": ("holding_current_percentage", int),
    "LoadingCurrentPercentage": ("loading_current_percentage", int),
    "MaximumAllowedCurrentPercentage": ("maximum_allowed_current_percentage", int),
    "MovingOverloadLimit": ("moving_overload_limit", int),
    "Mode_EnableLimits": ("mode_enable_limits", bool),
    "Mode_EnableStepAndDirection": ("mode_enable_step_and_direction", bool),
    "Mode_EnablePositionCorrection": ("mode_enable_position_correction", bool),
    "Mode_EnableMotorSlaveToEncoder": ("mode_enable_motor_slave_to_encoder", bool),
    "CoursePositionErrorWindow": ("course_position_error_window", int),
    "FinePositionErrorWindow": ("fine_position_error_window", int),
    "Gain": ("gain", int),
    "EncoderToMotorTickRatio": ("encoder_to_motor_tick_ratio", float),
    "BacklashCompensation": ("backlash_compensation", int),
    "MotorResponseTime": ("motor_response_time", int),
    "MMPerEncoderTick": ("mm_per_encoder_tick", float),
    "NumberOfEncoderTickPerRev": ("number_of_encoder_tick_per_rev", int),
  }


@dataclass
class GalvoConfig(_FromXmlMixin):
  """A galvanometer scan axis (``XGalvo`` / ``YGalvo``).

  Note: galvo sections carry no ``MotionName``; they are voltage-driven DAC axes.
  """

  config_version: int = 0
  controller_index: int = 0
  position_error_window: int = 0
  velocity_error_window: int = 0
  big_move_delay_ms: int = 0
  min_voltage: float = 0.0
  max_voltage: float = 0.0
  invert_voltage: bool = False
  enabled: bool = True
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "ConfigVersion": ("config_version", int),
    "ControllerIndex": ("controller_index", int),
    "PositionErrorWindow": ("position_error_window", int),
    "VelocityErrorWindow": ("velocity_error_window", int),
    "BigMoveDelayMS": ("big_move_delay_ms", int),
    "MinVoltage": ("min_voltage", float),
    "MaxVoltage": ("max_voltage", float),
    "InvertVoltage": ("invert_voltage", bool),
    "Enabled": ("enabled", bool),
  }


@dataclass
class FilterMapEntry(_FromXmlMixin):
  """One physical<->logical filter position mapping (``FilterMap``)."""

  logical_number: int = 0
  physical_number: int = 0
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "LogicalNumber": ("logical_number", int),
    "PhysicalNumber": ("physical_number", int),
  }


@dataclass
class FilterWheelConfig(_FromXmlMixin):
  """A discrete rotary filter wheel (``DichroicFilterWheel`` and friends)."""

  motion_name: str = ""
  config_version: int = 0
  number_of_filters: int = 0
  number_of_encoder_tick_per_rev: int = 0
  filter_map: List[FilterMapEntry] = field(default_factory=list)
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "MotionName": ("motion_name", str),
    "ConfigVersion": ("config_version", int),
    "NumberOfFilters": ("number_of_filters", int),
    "NumberOfEncoderTickPerRev": ("number_of_encoder_tick_per_rev", int),
  }

  @classmethod
  def from_element(cls, element: ET.Element) -> "FilterWheelConfig":
    obj = super().from_element(element)  # type: ignore[assignment]
    for child in element:
      if _localname(child.tag) == "FilterMap":
        obj.filter_map.append(FilterMapEntry.from_element(child))
    return obj


@dataclass
class IOChannelConfig(_FromXmlMixin):
  """An analog-in/analog-out/digital/lighting IO point in ``IOConfiguration``."""

  io_name: str = ""
  io_type: str = ""
  channel: int = 0
  bit_index: int = 0
  logical_number: int = 0
  physical_number: int = 0
  min_voltage: float = 0.0
  max_voltage: float = 0.0
  invert: bool = False
  invert_voltage: bool = False
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "IOName": ("io_name", str),
    "IOType": ("io_type", str),
    "Channel": ("channel", int),
    "BitIndex": ("bit_index", int),
    "LogicalNumber": ("logical_number", int),
    "PhysicalNumber": ("physical_number", int),
    "MinVoltage": ("min_voltage", float),
    "MaxVoltage": ("max_voltage", float),
    "Invert": ("invert", bool),
    "InvertVoltage": ("invert_voltage", bool),
  }


@dataclass
class IOConfig:
  """The board IO map: analog ins, digital IOs and lighting IOs."""

  analog_ins: List[IOChannelConfig] = field(default_factory=list)
  digital_ios: List[IOChannelConfig] = field(default_factory=list)
  lighting_ios: List[IOChannelConfig] = field(default_factory=list)

  # XML container tag -> attribute that collects its repeated children.
  _LIST_TAGS: ClassVar[Dict[str, str]] = {
    "AnalogIns": "analog_ins",
    "DigitalIOs": "digital_ios",
    "LightingIOs": "lighting_ios",
  }

  @classmethod
  def from_element(cls, element: ET.Element) -> "IOConfig":
    obj = cls()
    for child in element:
      attr = cls._LIST_TAGS.get(_localname(child.tag))
      if attr is not None:
        getattr(obj, attr).append(IOChannelConfig.from_element(child))
    return obj


@dataclass
class CeligoHardwareConfig:
  """Root hardware config, parsed from ``USBIOHardwareConfig.config``.

  Either load it from the Celigo install (:meth:`from_install` / :meth:`from_xml`) or
  build it directly in code and pass it to the backend.
  """

  x_axis: Optional[AxisConfig] = None
  y_axis: Optional[AxisConfig] = None
  z_axis: Optional[AxisConfig] = None
  x_galvo: Optional[GalvoConfig] = None
  y_galvo: Optional[GalvoConfig] = None
  dichroic_filter_wheel: Optional[FilterWheelConfig] = None
  io: Optional[IOConfig] = None
  source_path: Optional[str] = None

  # Default locations to search under an install root for the ConfigFiles dir.
  _CONFIG_SUBDIRS: ClassVar["tuple[str, ...]"] = (
    "ConfigFiles",
    os.path.join("Celigo", "ConfigFiles"),
    os.path.join("Nexcelom Bioscience", "Celigo", "ConfigFiles"),
  )
  _HARDWARE_FILE: ClassVar[str] = "USBIOHardwareConfig.config"

  @staticmethod
  def _inner_root(tree_root: ET.Element) -> ET.Element:
    """Descend through the .NET ``xmlSerializerSection`` envelope to the config body.

    Returns the element whose children are ``XAxis``/``YAxis``/... (i.e. the serialized
    ``USBIOConfigurationFile``). Tolerates the file being given with or without the
    ``<configuration>`` / ``<xmlSerializerSection>`` wrapper.
    """
    candidates = [tree_root] + list(tree_root.iter())
    for el in candidates:
      child_tags = {_localname(c.tag) for c in el}
      if "XAxis" in child_tags or "YAxis" in child_tags:
        return el
    return tree_root

  @classmethod
  def from_xml(cls, path: str) -> "CeligoHardwareConfig":
    """Parse a ``USBIOHardwareConfig.config`` file into a config object."""
    root = ET.parse(path).getroot()
    body = cls._inner_root(root)
    cfg = cls(source_path=os.path.abspath(path))
    for child in body:
      name = _localname(child.tag)
      if name == "XAxis":
        cfg.x_axis = AxisConfig.from_element(child)
      elif name == "YAxis":
        cfg.y_axis = AxisConfig.from_element(child)
      elif name == "ZSingleAxis":
        cfg.z_axis = AxisConfig.from_element(child)
      elif name == "XGalvo":
        cfg.x_galvo = GalvoConfig.from_element(child)
      elif name == "YGalvo":
        cfg.y_galvo = GalvoConfig.from_element(child)
      elif name == "DichroicFilterWheel":
        cfg.dichroic_filter_wheel = FilterWheelConfig.from_element(child)
      elif name == "IOConfiguration":
        cfg.io = IOConfig.from_element(child)
    return cfg

  @classmethod
  def from_install(cls, install_dir: Optional[str] = None) -> "CeligoHardwareConfig":
    """Locate and parse the Celigo hardware config (the default loading path).

    ``install_dir`` may point at the install root, a ``ConfigFiles`` directory, or be
    omitted to use the ``CELIGO_INSTALL_DIR`` environment variable. Raises
    :class:`FileNotFoundError` if the config file cannot be found.
    """
    path = cls._locate_hardware_file(install_dir)
    if path is None:
      raise FileNotFoundError(
        f"Could not find {cls._HARDWARE_FILE}. Pass install_dir= pointing at the "
        "Celigo install root or its ConfigFiles directory, or set CELIGO_INSTALL_DIR."
      )
    return cls.from_xml(path)

  @classmethod
  def _locate_hardware_file(cls, install_dir: Optional[str]) -> Optional[str]:
    return cls._locate_config_file(install_dir, cls._HARDWARE_FILE)

  @classmethod
  def _locate_config_file(cls, install_dir: Optional[str], filename: str) -> Optional[str]:
    roots: List[str] = []
    if install_dir is not None:
      roots.append(install_dir)
    env = os.environ.get("CELIGO_INSTALL_DIR")
    if env:
      roots.append(env)
    for root in roots:
      # direct file
      if os.path.isfile(root) and os.path.basename(root) == filename:
        return root
      # a ConfigFiles dir, or an install root containing one
      direct = os.path.join(root, filename)
      if os.path.isfile(direct):
        return direct
      for sub in cls._CONFIG_SUBDIRS:
        candidate = os.path.join(root, sub, filename)
        if os.path.isfile(candidate):
          return candidate
    return None


@dataclass
class ChannelDescriptor(_FromXmlMixin):
  """An imaging channel from ``ChannelConfig.xml`` (Default / HWAF / fluorescence)."""

  name: str = ""
  fixed_type: str = ""
  description: str = ""
  guid: str = ""
  calibration_index: int = 0
  channel_key: str = ""
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "Name": ("name", str),
    "FixedType": ("fixed_type", str),
    "Description": ("description", str),
    "ChannelDescGUID": ("guid", str),
    "CalibrationIndex": ("calibration_index", int),
    "GetChanDescKey": ("channel_key", str),
  }


def load_channels(path: str) -> List[ChannelDescriptor]:
  """Parse ``ChannelConfig.xml`` into a list of :class:`ChannelDescriptor`.

  Collects channel descriptors from the XML, deduplicating by GUID and preserving order.
  """
  root = ET.parse(path).getroot()
  channels: List[ChannelDescriptor] = []
  seen: set = set()
  for el in root.iter():
    if _localname(el.tag) == "ChannelDescriptor":
      ch = ChannelDescriptor.from_element(el)
      if ch.guid and ch.guid in seen:
        continue
      seen.add(ch.guid)
      channels.append(ch)
  return channels


@dataclass
class Calibrated2DCubicTransform:
  """A 2D cubic coordinate transform (``Calibrated2DCubicTranformation``).

  Each named polynomial term maps to an ``(x_coeff, y_coeff)`` pair. ``forward`` and
  ``reverse`` hold the two directions (e.g. position<->galvo volts). The exact way these
  terms are combined is documented alongside the transforms module; here we just load
  the coefficients faithfully.
  """

  forward: Dict[str, "tuple[float, float]"] = field(default_factory=dict)
  reverse: Dict[str, "tuple[float, float]"] = field(default_factory=dict)
  source_path: Optional[str] = None

  @staticmethod
  def _terms(direction_el: ET.Element) -> Dict[str, "tuple[float, float]"]:
    terms: Dict[str, "tuple[float, float]"] = {}
    for term in direction_el:
      name = _localname(term.tag)
      x = y = 0.0
      for comp in term:
        cn = _localname(comp.tag)
        if cn == "X":
          x = float(comp.text) if comp.text else 0.0
        elif cn == "Y":
          y = float(comp.text) if comp.text else 0.0
      terms[name] = (x, y)
    return terms

  @classmethod
  def from_xml(cls, path: str) -> "Calibrated2DCubicTransform":
    root = ET.parse(path).getroot()
    obj = cls(source_path=os.path.abspath(path))
    for el in root.iter():
      name = _localname(el.tag)
      if name == "Forward":
        obj.forward = cls._terms(el)
      elif name == "Reverse":
        obj.reverse = cls._terms(el)
    return obj


def load_galvo_calibration(path: str) -> Calibrated2DCubicTransform:
  """Parse ``GalvoCalibrationConfig.xml`` into a :class:`Calibrated2DCubicTransform`."""
  return Calibrated2DCubicTransform.from_xml(path)


@dataclass
class CalibrationConfig(_FromXmlMixin):
  """Per-machine optical/stage calibration (``CalibrationConfig.xml``).

  Feeds the pixel<->mm and sample-mm<->stage-mm affine transforms (see
  :mod:`pylabrobot.celigo.coordinates`).
  """

  microns_per_pixel_x: float = 1.0
  microns_per_pixel_y: float = 1.0
  image_width_pixels: int = 2048
  image_height_pixels: int = 2048
  image_to_stage_theta_radians: float = 0.0
  galvo_to_stage_theta_radians: float = 0.0
  calibrated_plate_corner_x: float = 0.0
  calibrated_plate_corner_y: float = 0.0
  calibrated_plate_to_stage_theta_radians: float = 0.0
  stage_x_scale: float = 1.0
  stage_y_scale: float = 1.0
  stage_shear: float = 0.0
  stage_x_shear_offset: float = 0.0
  stage_y_shear_offset: float = 0.0
  calibrated_z_position: float = 0.0
  calibrated_z_glass_plate_delta: float = 0.0
  z_plane_x_coeff: float = 0.0
  z_plane_y_coeff: float = 0.0
  source_path: Optional[str] = None
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "MicronsPerPixelX": ("microns_per_pixel_x", float),
    "MicronsPerPixelY": ("microns_per_pixel_y", float),
    "ImageWidthPixels": ("image_width_pixels", int),
    "ImageHeightPixels": ("image_height_pixels", int),
    "ImageToStageThetaRadians": ("image_to_stage_theta_radians", float),
    "GalvoToStageThetaRadians": ("galvo_to_stage_theta_radians", float),
    "CalibratedPlateCornerX": ("calibrated_plate_corner_x", float),
    "CalibratedPlateCornerY": ("calibrated_plate_corner_y", float),
    "CalibratedPlateToStageThetaRadians": ("calibrated_plate_to_stage_theta_radians", float),
    "StageXScale": ("stage_x_scale", float),
    "StageYScale": ("stage_y_scale", float),
    "StageShear": ("stage_shear", float),
    "StageXShearOffset": ("stage_x_shear_offset", float),
    "StageYShearOffset": ("stage_y_shear_offset", float),
    "CalibratedZPosition": ("calibrated_z_position", float),
    "CalibratedZGlassPlateDelta": ("calibrated_z_glass_plate_delta", float),
    "ZPlaneXCoeff": ("z_plane_x_coeff", float),
    "ZPlaneYCoeff": ("z_plane_y_coeff", float),
  }

  @classmethod
  def from_xml(cls, path: str) -> "CalibrationConfig":
    root = ET.parse(path).getroot()
    obj = cls.from_scalars(_all_leaf_scalars(root))
    obj.source_path = os.path.abspath(path)
    return obj


@dataclass
class HardwareDefaultConfig(_FromXmlMixin):
  """Instrument defaults (``HardwareDefaultConfig.xml``): plate corner, FOV, galvo MM/V."""

  default_calibrated_z: float = 0.0
  default_plate_x_corner_stage_coordinate: float = 0.0
  default_plate_y_corner_stage_coordinate: float = 0.0
  default_x_field_of_view_mm: float = 0.0
  default_y_field_of_view_mm: float = 0.0
  default_x_galvo_mm_per_volt: float = 0.0
  default_y_galvo_mm_per_volt: float = 0.0
  source_path: Optional[str] = None
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "DefaultCalibratedZ": ("default_calibrated_z", float),
    "DefaultPlateXCornerStageCoordinate": ("default_plate_x_corner_stage_coordinate", float),
    "DefaultPlateYCornerStageCoordinate": ("default_plate_y_corner_stage_coordinate", float),
    "DefaultXFieldOfViewMM": ("default_x_field_of_view_mm", float),
    "DefaultYFieldOfViewMM": ("default_y_field_of_view_mm", float),
    "DefaultXGalvoMMPerVolt": ("default_x_galvo_mm_per_volt", float),
    "DefaultYGalvoMMPerVolt": ("default_y_galvo_mm_per_volt", float),
  }

  @classmethod
  def from_xml(cls, path: str) -> "HardwareDefaultConfig":
    root = ET.parse(path).getroot()
    obj = cls.from_scalars(_all_leaf_scalars(root))
    obj.source_path = os.path.abspath(path)
    return obj


def load_calibration(path: str) -> CalibrationConfig:
  """Parse ``CalibrationConfig.xml`` into a :class:`CalibrationConfig`."""
  return CalibrationConfig.from_xml(path)


def load_hardware_defaults(path: str) -> HardwareDefaultConfig:
  """Parse ``HardwareDefaultConfig.xml`` into a :class:`HardwareDefaultConfig`."""
  return HardwareDefaultConfig.from_xml(path)
