"""BenchCel labware calculation and PyLabRobot plate integration helpers.

BenchCel/VWorks labware settings separate three geometry concepts that are easy
to conflate:

* ``StackingThickness`` is the vertical pitch of plates in a nested stack.
* PLR ``Plate.size_z`` is the full outside height of one plate.
* ``RobotGripperOffset`` is the BenchCel robot gripper contact height measured
  from the bottom of the plate.

This module does not bundle per-catalog BenchCel XML profiles. Instead, it
calculates a conservative BenchCel geometry profile from a PLR plate resource and
allows explicit overrides for values that cannot be inferred from dimensions
alone, such as optical sensor thresholds.
"""

from __future__ import annotations

import dataclasses
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Union

from pylabrobot.resources import Coordinate, Plate

DEVICE_PAYLOAD_LENGTH = 77
DEFAULT_NESTING_OVERLAP = 1.5
DEFAULT_MIN_ROBOT_GRIPPER_OFFSET = 5.0
DEFAULT_MAX_ROBOT_GRIPPER_OFFSET = 8.0
DEFAULT_MIN_PICKUP_DISTANCE_FROM_TOP = 5.4
DEFAULT_LOW_PROFILE_HEIGHT_CUTOFF = 11.5
DEFAULT_TALL_PLATE_HEIGHT_CUTOFF = 30.0
DEFAULT_ADDITIONAL_RELEASE_HEIGHT = 2.0


@dataclasses.dataclass(frozen=True)
class PlateNotchSettings:
  """Orientation-notch settings from BenchCel/VWorks labware XML or overrides."""

  check_orientation: bool = True
  a1_notch: bool = True
  top_right_notch: bool = False
  bottom_left_notch: bool = True
  bottom_right_notch: bool = False


@dataclasses.dataclass(frozen=True)
class BenchCelLabwareSettings:
  """BenchCel/VWorks labware settings paired with PLR plate dimensions.

  Args:
    name: Human-readable labware name.
    plate_size_x: Full outside plate length in mm.
    plate_size_y: Full outside plate width in mm.
    plate_size_z: Full outside plate height in mm.
    stacking_thickness: Vertical pitch of nested plates in the BenchCel stacker.
      This is usually smaller than full plate height because plates nest.
    robot_gripper_offset: BenchCel robot gripper contact height from the bottom
      of the plate. This maps to ``Plate.preferred_pickup_location.z``.
    stacker_gripper_offset: Stacker clamp/gripper contact height from the bottom
      of the plate.
  """

  name: str
  plate_size_x: float
  plate_size_y: float
  plate_size_z: float
  stacking_thickness: float
  robot_gripper_offset: float
  stacker_gripper_offset: float
  sensor_offset: float
  gripper_open_position: float = -1.0
  gripper_holding_plate_position: float = 8.0
  gripper_holding_stack_position: float = 8.5
  orientation_sensor_threshold: int = 100
  plate_presence_threshold: int = 225
  sensor_intensity: int = 50
  error_correction_offset: float = 0.0
  can_be_lidded: bool = False
  lidded_plate_stacking_thickness: Optional[float] = None
  lidded_plate_thickness: Optional[float] = None
  lidded_plate_resting_height: Optional[float] = None
  lidded_plate_gripper_offset: Optional[float] = None
  lidded_plate_gripper_position: Optional[float] = None
  lidded_plate_departure_height: Optional[float] = None
  can_be_sealed: bool = False
  sealed_plate_stacking_thickness: Optional[float] = None
  sealed_plate_thickness: Optional[float] = None
  stack_plate_presence_threshold: int = 50
  rack_presence_threshold: int = 3
  additional_release_height: float = DEFAULT_ADDITIONAL_RELEASE_HEIGHT
  low_pressure_warning: int = 30
  tilt_margin: float = 2.0
  tilt_margin_enabled: bool = False
  notch_settings: PlateNotchSettings = dataclasses.field(default_factory=PlateNotchSettings)
  identifier: Optional[str] = None
  source: Optional[str] = None

  @classmethod
  def from_plate(cls, plate: Plate, **kwargs) -> "BenchCelLabwareSettings":
    """Calculate BenchCel settings from a PLR plate resource."""
    return calculate_benchcel_labware_settings(plate, **kwargs)

  @classmethod
  def from_xml_file(
    cls,
    path: Union[str, Path],
    *,
    plate_size_x: Optional[float] = None,
    plate_size_y: Optional[float] = None,
    plate_size_z: Optional[float] = None,
    identifier: Optional[str] = None,
  ) -> "BenchCelLabwareSettings":
    """Parse a user-supplied BenchCel/VWorks XML file.

    XML files contain BenchCel stack/gripper values but not necessarily the full
    physical plate dimensions PLR needs. Provide ``plate_size_*`` values when the
    XML should be used to validate or annotate PLR plate resources.
    """
    path = Path(path)
    root = ET.parse(path).getroot()
    labware = root.find("Labware")
    if labware is None:
      raise ValueError(f"BenchCel labware XML has no <Labware> section: {path}")
    stack = root.find("StackSettings")
    notch = labware.find("PlateNotchesOrientationOptions")

    def text(parent: ET.Element, name: str) -> str:
      element = parent.find(name)
      if element is None or element.text is None:
        raise ValueError(f"Missing <{name}> in {path}")
      return element.text.strip()

    def optional_text(parent: Optional[ET.Element], name: str) -> Optional[str]:
      if parent is None:
        return None
      element = parent.find(name)
      if element is None or element.text is None:
        return None
      value = element.text.strip()
      return value if value != "" else None

    def as_bool(value: Optional[str]) -> bool:
      return value is not None and value.strip().lower() in {"yes", "true", "1", "enabled"}

    def as_optional_float(value: Optional[str]) -> Optional[float]:
      return None if value is None else float(value)

    def as_optional_int(value: Optional[str]) -> Optional[int]:
      return None if value is None else int(value)

    name = text(labware, "Name")
    stacking_thickness = float(text(labware, "StackingThickness"))
    robot_gripper_offset = float(text(labware, "RobotGripperOffset"))
    stacker_gripper_offset = float(text(labware, "StackerGripperOffset"))
    sensor_offset = float(text(labware, "SensorOffset"))

    # If full physical height is not supplied, use stack pitch as the best-known
    # fallback. Callers should pass real dimensions for PLR integration.
    px = 127.76 if plate_size_x is None else plate_size_x
    py = 85.48 if plate_size_y is None else plate_size_y
    pz = stacking_thickness if plate_size_z is None else plate_size_z

    tilt = stack.find("TiltMargin") if stack is not None else None
    notch_settings = PlateNotchSettings()
    if notch is not None:
      notch_settings = PlateNotchSettings(
        check_orientation=as_bool(optional_text(notch, "CheckOrientation")),
        a1_notch=as_bool(optional_text(notch, "A1Notch")),
        top_right_notch=as_bool(optional_text(notch, "TopRightNotch")),
        bottom_left_notch=as_bool(optional_text(notch, "BottomLeftNotch")),
        bottom_right_notch=as_bool(optional_text(notch, "BottomRightNotch")),
      )

    return cls(
      identifier=identifier or path.stem,
      name=name,
      plate_size_x=px,
      plate_size_y=py,
      plate_size_z=pz,
      stacking_thickness=stacking_thickness,
      robot_gripper_offset=robot_gripper_offset,
      stacker_gripper_offset=stacker_gripper_offset,
      sensor_offset=sensor_offset,
      gripper_open_position=float(text(labware, "GripperOpenPosition")),
      gripper_holding_plate_position=float(text(labware, "GripperHoldingPlatePosition")),
      gripper_holding_stack_position=float(text(labware, "GripperHoldingStackPosition")),
      orientation_sensor_threshold=int(text(labware, "OrientationSensorThreshold")),
      plate_presence_threshold=int(text(labware, "PlatePresenceThreshold")),
      sensor_intensity=int(text(labware, "SensorIntensity")),
      error_correction_offset=float(text(labware, "ErrorCorrectionOffset")),
      can_be_lidded=as_bool(optional_text(labware, "CanBeLidded")),
      lidded_plate_stacking_thickness=as_optional_float(
        optional_text(labware, "LiddedPlateStackingThickness")
      ),
      lidded_plate_thickness=as_optional_float(optional_text(labware, "LiddedPlateThickness")),
      lidded_plate_resting_height=as_optional_float(
        optional_text(labware, "LiddedPlateRestingHeight")
      ),
      lidded_plate_gripper_offset=as_optional_float(
        optional_text(labware, "LiddedPlateGripperOffset")
      ),
      lidded_plate_gripper_position=as_optional_float(
        optional_text(labware, "LiddedPlateGripperPosition")
      ),
      lidded_plate_departure_height=as_optional_float(
        optional_text(labware, "LiddedPlateDepartureHeight")
      ),
      can_be_sealed=as_bool(optional_text(labware, "CanBeSealed")),
      sealed_plate_stacking_thickness=as_optional_float(
        optional_text(labware, "SealedPlateStackingThickness")
      ),
      sealed_plate_thickness=as_optional_float(optional_text(labware, "SealedPlateThickness")),
      stack_plate_presence_threshold=as_optional_int(optional_text(stack, "PlatePresenceThreshold"))
      or 50,
      rack_presence_threshold=as_optional_int(optional_text(stack, "RackPresenceThreshold")) or 3,
      additional_release_height=as_optional_float(optional_text(stack, "AdditionalReleaseHeight"))
      or DEFAULT_ADDITIONAL_RELEASE_HEIGHT,
      low_pressure_warning=as_optional_int(optional_text(stack, "LowPressureWarning")) or 30,
      tilt_margin=as_optional_float(tilt.text.strip() if tilt is not None and tilt.text else None)
      or 2.0,
      tilt_margin_enabled=as_bool(tilt.get("Enabled") if tilt is not None else None),
      notch_settings=notch_settings,
      source=str(path),
    )

  @classmethod
  def from_dict(cls, data: dict) -> "BenchCelLabwareSettings":
    """Deserialize settings from :meth:`to_dict`."""
    data = dict(data)
    notch = data.get("notch_settings")
    if isinstance(notch, dict):
      data["notch_settings"] = PlateNotchSettings(**notch)
    return cls(**data)

  def to_dict(self) -> dict:
    """Return JSON-serialisable settings."""
    return dataclasses.asdict(self)

  def effective_stacking_thickness(self, *, sealed: bool = False, lidded: bool = False) -> float:
    """Return the BenchCel stack pitch for the selected labware state."""
    sealed_pitch = self.sealed_plate_stacking_thickness
    if sealed and sealed_pitch not in (None, 0):
      return float(sealed_pitch)  # type: ignore[arg-type]
    lidded_pitch = self.lidded_plate_stacking_thickness
    if lidded and lidded_pitch not in (None, 0):
      return float(lidded_pitch)  # type: ignore[arg-type]
    return self.stacking_thickness

  def effective_plate_height(self, *, sealed: bool = False, lidded: bool = False) -> float:
    """Return full outside plate height for PLR rack/site sizing."""
    sealed_height = self.sealed_plate_thickness
    if sealed and sealed_height not in (None, 0):
      return float(sealed_height)  # type: ignore[arg-type]
    lidded_height = self.lidded_plate_thickness
    if lidded and lidded_height not in (None, 0):
      return float(lidded_height)  # type: ignore[arg-type]
    return self.plate_size_z

  def robot_pickup_distance_from_top(self, *, sealed: bool = False, lidded: bool = False) -> float:
    """Return PLR ``pickup_distance_from_top`` implied by ``RobotGripperOffset``."""
    return self.effective_plate_height(sealed=sealed, lidded=lidded) - self.robot_gripper_offset

  def preferred_pickup_location(self, plate: Plate) -> Coordinate:
    """Return a PLR preferred pickup location using the BenchCel robot offset."""
    return Coordinate(
      x=plate.get_size_x() / 2,
      y=plate.get_size_y() / 2,
      z=self.robot_gripper_offset,
    )

  def dimension_differences(self, plate: Plate) -> dict[str, float]:
    """Return profile minus plate-resource dimensions for each axis."""
    return {
      "x": self.plate_size_x - plate.get_size_x(),
      "y": self.plate_size_y - plate.get_size_y(),
      "z": self.plate_size_z - plate.get_size_z(),
    }

  def validate_plate_dimensions(self, plate: Plate, *, tolerance_mm: float = 0.25) -> None:
    """Raise if a PLR plate resource differs too far from this BenchCel profile."""
    differences = self.dimension_differences(plate)
    failures = [
      f"{axis}: expected {expected:.3f} mm, got {actual:.3f} mm"
      for axis, expected, actual in (
        ("x", self.plate_size_x, plate.get_size_x()),
        ("y", self.plate_size_y, plate.get_size_y()),
        ("z", self.plate_size_z, plate.get_size_z()),
      )
      if abs(differences[axis]) > tolerance_mm
    ]
    if failures:
      raise ValueError(
        f"Plate {plate.name!r} does not match BenchCel labware {self.name!r}: "
        + "; ".join(failures)
      )

  def apply_to_plate(
    self,
    plate: Plate,
    *,
    validate_dimensions: bool = True,
    tolerance_mm: float = 0.25,
  ) -> Plate:
    """Set PLR pickup metadata on ``plate`` using this BenchCel profile."""
    if validate_dimensions:
      self.validate_plate_dimensions(plate, tolerance_mm=tolerance_mm)
    plate.preferred_pickup_location = self.preferred_pickup_location(plate)
    return plate

  def to_device_payload(self) -> bytes:
    """Encode the 77-byte ``0x7d`` BenchCel labware-settings payload.

    The layout was reverse-engineered from VWorks packet captures and validated
    byte-for-byte against several real plates. Fields that were always zero in
    the captures for standard flat microplates (``ErrorCorrectionOffset`` and the
    lidded/sealed sub-fields) are sent as zero and are not yet mapped.
    """
    if not 0 <= int(self.orientation_sensor_threshold) <= 0xFFFF:
      raise ValueError("orientation_sensor_threshold must fit in uint16")
    if not 0 <= int(self.sensor_intensity) <= 0xFFFF:
      raise ValueError("sensor_intensity must fit in uint16")
    if not 0 <= int(self.plate_presence_threshold) <= 0xFFFF:
      raise ValueError("plate_presence_threshold must fit in uint16")

    notch = self.notch_settings
    payload = bytearray(DEVICE_PAYLOAD_LENGTH)
    struct.pack_into("<f", payload, 0, float(self.stacking_thickness))
    struct.pack_into("<f", payload, 4, float(self.robot_gripper_offset))
    struct.pack_into("<f", payload, 8, float(self.stacker_gripper_offset))
    struct.pack_into("<f", payload, 12, float(self.sensor_offset))
    payload[16] = 1 if notch.a1_notch else 0
    payload[17] = 1 if notch.top_right_notch else 0
    payload[18] = 1 if notch.bottom_left_notch else 0
    payload[19] = 1 if notch.bottom_right_notch else 0
    struct.pack_into("<H", payload, 20, int(self.orientation_sensor_threshold))
    struct.pack_into("<H", payload, 22, int(self.sensor_intensity))
    struct.pack_into("<f", payload, 24, float(self.gripper_open_position))
    struct.pack_into("<f", payload, 28, float(self.gripper_holding_plate_position))
    struct.pack_into("<f", payload, 32, float(self.gripper_holding_stack_position))
    payload[36] = 1 if notch.check_orientation else 0
    struct.pack_into("<f", payload, 37, float(self.plate_size_z))
    payload[69] = 1 if notch.a1_notch else 0  # observed duplicate of A1Notch
    struct.pack_into("<H", payload, 75, int(self.plate_presence_threshold))
    return bytes(payload)

  @classmethod
  def from_device_payload(
    cls,
    payload: bytes,
    *,
    name: str = "device",
    plate_size_x: float = 127.76,
    plate_size_y: float = 85.48,
  ) -> "BenchCelLabwareSettings":
    """Decode a 77-byte ``0x7d`` payload back into settings.

    Only the confidently-mapped fields are recovered; unmapped lidded/sealed
    fields stay at their defaults.
    """
    if len(payload) != DEVICE_PAYLOAD_LENGTH:
      raise ValueError(f"expected {DEVICE_PAYLOAD_LENGTH}-byte 0x7d payload, got {len(payload)}")

    def fp(o: int) -> float:
      return float(struct.unpack_from("<f", payload, o)[0])

    def up(o: int) -> int:
      return int(struct.unpack_from("<H", payload, o)[0])

    return cls(
      name=name,
      plate_size_x=plate_size_x,
      plate_size_y=plate_size_y,
      plate_size_z=fp(37),
      stacking_thickness=fp(0),
      robot_gripper_offset=fp(4),
      stacker_gripper_offset=fp(8),
      sensor_offset=fp(12),
      gripper_open_position=fp(24),
      gripper_holding_plate_position=fp(28),
      gripper_holding_stack_position=fp(32),
      orientation_sensor_threshold=up(20),
      sensor_intensity=up(22),
      plate_presence_threshold=up(75),
      notch_settings=PlateNotchSettings(
        check_orientation=bool(payload[36]),
        a1_notch=bool(payload[16]),
        top_right_notch=bool(payload[17]),
        bottom_left_notch=bool(payload[18]),
        bottom_right_notch=bool(payload[19]),
      ),
      source="decoded from 0x7d payload",
    )


def calculate_stacking_thickness(
  plate_height: float,
  *,
  nesting_overlap: float = DEFAULT_NESTING_OVERLAP,
) -> float:
  """Estimate BenchCel ``StackingThickness`` from full plate height.

  The default overlap (1.5 mm) matches the supplied example XML/dimension pairs
  within about 0.2 mm. Override this for labware with unusual nesting behavior.
  """
  if plate_height <= 0:
    raise ValueError(f"plate_height must be positive, got {plate_height}")
  if nesting_overlap < 0:
    raise ValueError(f"nesting_overlap must be non-negative, got {nesting_overlap}")
  if nesting_overlap >= plate_height:
    raise ValueError(
      f"nesting_overlap ({nesting_overlap}) must be smaller than plate_height ({plate_height})"
    )
  return plate_height - nesting_overlap


def calculate_robot_gripper_offset(
  plate_height: float,
  *,
  min_offset: float = DEFAULT_MIN_ROBOT_GRIPPER_OFFSET,
  max_offset: float = DEFAULT_MAX_ROBOT_GRIPPER_OFFSET,
  min_pickup_distance_from_top: float = DEFAULT_MIN_PICKUP_DISTANCE_FROM_TOP,
) -> float:
  """Estimate BenchCel ``RobotGripperOffset`` from plate height.

  The BenchCel manual says plates are typically gripped 5-10 mm above the bottom.
  The default calculation keeps at least ~5.4 mm above the grip point where
  possible while capping the grip height at 8 mm from the bottom.
  """
  if plate_height <= 0:
    raise ValueError(f"plate_height must be positive, got {plate_height}")
  if min_offset > max_offset:
    raise ValueError("min_offset must be <= max_offset")
  return max(min_offset, min(max_offset, plate_height - min_pickup_distance_from_top))


def calculate_stacker_gripper_offset(
  plate_height: float,
  robot_gripper_offset: float,
  *,
  low_profile_height_cutoff: float = DEFAULT_LOW_PROFILE_HEIGHT_CUTOFF,
  tall_plate_height_cutoff: float = DEFAULT_TALL_PLATE_HEIGHT_CUTOFF,
) -> float:
  """Estimate BenchCel ``StackerGripperOffset`` from plate height.

  This is a heuristic: low plates need clamps lower, very tall plates can use a
  slightly higher clamp point, and standard SBS microplates sit in between.
  """
  if plate_height <= low_profile_height_cutoff:
    return min(robot_gripper_offset, 4.0)
  if plate_height >= tall_plate_height_cutoff:
    return min(robot_gripper_offset, 6.0)
  return min(robot_gripper_offset, 5.0)


def calculate_sensor_offset(
  plate_height: float,
  *,
  low_profile_height_cutoff: float = DEFAULT_LOW_PROFILE_HEIGHT_CUTOFF,
  tall_plate_height_cutoff: float = DEFAULT_TALL_PLATE_HEIGHT_CUTOFF,
) -> float:
  """Estimate BenchCel ``SensorOffset`` from plate height."""
  if plate_height <= low_profile_height_cutoff:
    return 7.0
  if plate_height >= tall_plate_height_cutoff:
    return max(7.0, plate_height - 4.0)
  return 8.0


def calculate_benchcel_labware_settings(
  plate: Plate,
  *,
  name: Optional[str] = None,
  identifier: Optional[str] = None,
  nesting_overlap: float = DEFAULT_NESTING_OVERLAP,
  stacking_thickness: Optional[float] = None,
  robot_gripper_offset: Optional[float] = None,
  stacker_gripper_offset: Optional[float] = None,
  sensor_offset: Optional[float] = None,
  orientation_sensor_threshold: int = 100,
  plate_presence_threshold: int = 225,
  sensor_intensity: int = 50,
  error_correction_offset: float = 0.0,
  gripper_open_position: float = -1.0,
  gripper_holding_plate_position: float = 8.0,
  gripper_holding_stack_position: float = 8.5,
  can_be_lidded: Optional[bool] = None,
  can_be_sealed: bool = False,
  sealed_plate_stacking_thickness: Optional[float] = None,
  sealed_plate_thickness: Optional[float] = None,
  notch_settings: Optional[PlateNotchSettings] = None,
) -> BenchCelLabwareSettings:
  """Calculate BenchCel labware settings from a PLR plate resource.

  Geometry fields are calculated from ``plate``. Optical sensor thresholds and
  notch options cannot be reliably inferred from dimensions, so they are exposed
  as optional overrides with conservative defaults.
  """
  height = plate.get_size_z()
  robot_offset = robot_gripper_offset
  if robot_offset is None:
    robot_offset = calculate_robot_gripper_offset(height)
  stacker_offset = stacker_gripper_offset
  if stacker_offset is None:
    stacker_offset = calculate_stacker_gripper_offset(height, robot_offset)
  sensor = sensor_offset
  if sensor is None:
    sensor = calculate_sensor_offset(height)

  # BenchCel ``StackingThickness`` is the per-plate vertical pitch of a nested stack, which is
  # exactly PLR ``Plate.stacking_z_height``. Prefer an explicit override, then the plate's own
  # declared pitch, and only estimate from height (``size_z - nesting_overlap``) as a last resort.
  resolved_stacking_thickness = stacking_thickness
  if resolved_stacking_thickness is None:
    resolved_stacking_thickness = plate.stacking_z_height
  if resolved_stacking_thickness is None:
    resolved_stacking_thickness = calculate_stacking_thickness(
      height, nesting_overlap=nesting_overlap
    )

  return BenchCelLabwareSettings(
    identifier=identifier,
    name=name or plate.model or plate.name,
    plate_size_x=plate.get_size_x(),
    plate_size_y=plate.get_size_y(),
    plate_size_z=height,
    stacking_thickness=resolved_stacking_thickness,
    robot_gripper_offset=robot_offset,
    stacker_gripper_offset=stacker_offset,
    sensor_offset=sensor,
    gripper_open_position=gripper_open_position,
    gripper_holding_plate_position=gripper_holding_plate_position,
    gripper_holding_stack_position=gripper_holding_stack_position,
    orientation_sensor_threshold=orientation_sensor_threshold,
    plate_presence_threshold=plate_presence_threshold,
    sensor_intensity=sensor_intensity,
    error_correction_offset=error_correction_offset,
    can_be_lidded=plate.has_lid() if can_be_lidded is None else can_be_lidded,
    can_be_sealed=can_be_sealed,
    sealed_plate_stacking_thickness=sealed_plate_stacking_thickness,
    sealed_plate_thickness=sealed_plate_thickness,
    notch_settings=notch_settings or PlateNotchSettings(),
    source="calculated from PLR plate dimensions",
  )


def resolve_benchcel_labware_settings(
  labware: Union[Plate, BenchCelLabwareSettings, dict],
) -> BenchCelLabwareSettings:
  """Resolve a PLR plate, settings object, or serialized settings dict."""
  if isinstance(labware, BenchCelLabwareSettings):
    return labware
  if isinstance(labware, Plate):
    return calculate_benchcel_labware_settings(labware)
  if isinstance(labware, dict):
    return BenchCelLabwareSettings.from_dict(labware)
  raise TypeError(
    "labware must be a Plate, BenchCelLabwareSettings, or serialized settings dict; "
    f"got {type(labware).__name__}"
  )


def apply_benchcel_labware_settings(
  plate: Plate,
  labware: Optional[Union[BenchCelLabwareSettings, dict]] = None,
  *,
  validate_dimensions: bool = True,
  tolerance_mm: float = 0.25,
  **calculation_kwargs,
) -> BenchCelLabwareSettings:
  """Apply BenchCel pickup metadata to ``plate`` and return the settings used.

  If ``labware`` is omitted, settings are calculated from the PLR plate
  dimensions using :func:`calculate_benchcel_labware_settings`.
  """
  settings = (
    calculate_benchcel_labware_settings(plate, **calculation_kwargs)
    if labware is None
    else resolve_benchcel_labware_settings(labware)
  )
  settings.apply_to_plate(
    plate,
    validate_dimensions=validate_dimensions,
    tolerance_mm=tolerance_mm,
  )
  return settings


def benchcel_labware_summary_row(settings: BenchCelLabwareSettings) -> dict:
  """Return one summary row useful for docs/tests/diagnostics."""
  height = settings.effective_plate_height()
  return {
    "name": settings.name,
    "plate_height": height,
    "stacking_thickness": settings.stacking_thickness,
    "nesting_overlap": height - settings.stacking_thickness,
    "robot_gripper_offset": settings.robot_gripper_offset,
    "pickup_distance_from_top": settings.robot_pickup_distance_from_top(),
    "stacker_gripper_offset": settings.stacker_gripper_offset,
    "sensor_offset": settings.sensor_offset,
  }
