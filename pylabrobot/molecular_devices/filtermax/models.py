"""Public models used by the FilterMax F5 driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Literal, Mapping, Optional, Tuple

FilterKind = Literal["excitation", "emission"]
FilterTechnique = Literal[
  "absorbance",
  "fluorescence",
  "fluorescence_polarization",
  "time_resolved_fluorescence",
  "luminescence",
]
ShakePattern = Literal["linear", "orbital"]
ShakeSpeed = Literal["low", "medium", "high"]
WellScanPattern = Literal["horizontal", "fill"]
PlateOrientation = Literal["landscape", "portrait"]


@dataclass(frozen=True)
class InstrumentInfo:
  """Instrument identity, firmware, installed slide IDs, and reported options."""

  model: str
  firmware: str
  device_number: int
  serial_number: str
  excitation_slide_id: int
  emission_slide_id: int
  device_code: int
  plate_control: bool
  pic_firmware: str
  cpld_version: int
  raw: str = field(repr=False)


@dataclass(frozen=True)
class FilterMaxStatus:
  """Readiness text and the raw SGS firmware state bits."""

  ready: bool
  text: str
  state_bits: Tuple[int, ...]


@dataclass(frozen=True)
class InstalledFilterSlides:
  """Installed excitation and emission slide identifiers."""

  excitation_slide_id: int
  emission_slide_id: int


@dataclass(frozen=True)
class FilterDefinition:
  """One catalog filter; wavelengths and bandwidths are in nm and positions are one-based."""

  kind: FilterKind
  slide_id: int
  slide_name: str
  position: int
  wavelength: int
  bandwidth: int
  apply_to: int
  techniques: FrozenSet[FilterTechnique]
  molecular_devices_number: Optional[str] = None


@dataclass(frozen=True)
class FilterSelection:
  """Resolved slide and one-based slot, with wavelength and bandwidth in nm."""

  kind: FilterKind
  slide_id: int
  position: int
  wavelength: int
  bandwidth: int


@dataclass(frozen=True)
class KineticTiming:
  """Number of cycles and their nominal start-to-start interval in seconds."""

  interval: float
  reads: int

  def __post_init__(self) -> None:
    """Validate the supported settings."""
    if self.interval <= 0:
      raise ValueError("interval must be positive")
    if self.reads < 1:
      raise ValueError("reads must be at least 1")


@dataclass(frozen=True)
class ShakingSettings:
  """Finite shaking for duration seconds, optionally repeated between kinetic reads."""

  pattern: ShakePattern
  speed: ShakeSpeed
  duration: int
  between_reads: bool = False

  def __post_init__(self) -> None:
    """Validate the supported settings."""
    if self.duration < 1:
      raise ValueError("duration must be at least 1")


@dataclass(frozen=True)
class WellScanSettings:
  """Horizontal or circular-fill sampling on a grid with density points per axis.

  Grid positions do not imply a physical distance. Horizontal scans have one row;
  fill scans sample a square grid and retain its circular mask.
  """

  pattern: WellScanPattern
  density: int

  def __post_init__(self) -> None:
    """Validate the supported settings."""
    if self.density < 1:
      raise ValueError("density must be at least 1")

  @property
  def grid_shape(self) -> Tuple[int, int]:
    """Return the number of grid columns and rows requested from the reader."""
    return (self.density, 1) if self.pattern == "horizontal" else (self.density, self.density)


@dataclass(frozen=True)
class PlateGeometry:
  """FilterMax plate geometry in public PLR units (millimetres)."""

  rows: int
  columns: int
  length: float
  width: float
  height: float
  well_depth: float
  left_column_offset: float
  top_row_offset: float
  column_spacing: float
  row_spacing: float
  well_size_x: float
  well_size_y: float
  absorbance_z: float
  name: str = "Custom plate"
  orientation: PlateOrientation = "landscape"

  def __post_init__(self) -> None:
    """Validate the supported settings."""
    if self.orientation not in ("landscape", "portrait"):
      raise ValueError(f"Unsupported FilterMax plate orientation {self.orientation!r}")

  @classmethod
  def costar_96_clear_landscape(cls) -> "PlateGeometry":
    """Return the live-optimized geometry captured from the validation plate."""

    return cls(
      rows=8,
      columns=12,
      length=127.70,
      width=85.70,
      height=14.27,
      well_depth=10.69,
      left_column_offset=14.05,
      top_row_offset=11.18,
      column_spacing=9.02,
      row_spacing=9.00,
      well_size_x=6.40,
      well_size_y=6.40,
      absorbance_z=10.27,
      name="96 Well Costar clear [Landscape]",
    )

  @classmethod
  def costar_96_clear_portrait(cls) -> "PlateGeometry":
    """Return the Costar 96-well geometry with portrait scan orientation."""

    return cls(
      rows=8,
      columns=12,
      length=127.70,
      width=85.70,
      height=14.27,
      well_depth=10.69,
      left_column_offset=14.05,
      top_row_offset=11.18,
      column_spacing=9.02,
      row_spacing=9.00,
      well_size_x=6.40,
      well_size_y=6.40,
      absorbance_z=10.27,
      name="96 Well Costar clear [Portrait]",
      orientation="portrait",
    )


@dataclass(frozen=True)
class WellScanPoint:
  """One optical-density measurement at zero-based positions in the scan grid.

  grid_column and grid_row are integer indices, not millimetres. Fill-scan indices
  refer to the full square grid; horizontal scans use grid_row=0.
  """

  grid_column: int
  grid_row: int
  value: float


PlateData = List[List[Optional[float]]]


@dataclass(frozen=True)
class AbsorbanceResult:
  """Row-major optical densities, with unselected wells represented by None.

  wavelengths are in nm; temperature is in degrees Celsius; timestamp is Unix
  seconds at command submission. elapsed_time is the nominal kinetic timepoint
  in seconds, not measured latency. scan_points preserves the retained grid samples.
  """

  data: PlateData
  wavelengths: Tuple[int, ...]
  reference_subtracted: bool
  temperature: Optional[float]
  timestamp: float
  elapsed_time: float = 0.0
  scan_points: Mapping[str, Tuple[WellScanPoint, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LuminescenceResult:
  """Row-major raw RLU for a one-based channel; unselected wells are None.

  temperature is in degrees Celsius; timestamp is Unix seconds at command
  submission; elapsed_time is the nominal kinetic timepoint in seconds.
  """

  data: PlateData
  channel: int
  temperature: Optional[float]
  timestamp: float
  elapsed_time: float = 0.0


@dataclass(frozen=True)
class FluorescenceResult:
  """Reserved fluorescence result with wavelengths in nm; acquisition is unsupported.

  data is row-major with None for unselected wells. temperature is in degrees
  Celsius, timestamp in Unix seconds, and elapsed_time in nominal kinetic seconds.
  """

  data: PlateData
  excitation_wavelength: int
  emission_wavelength: int
  temperature: Optional[float]
  timestamp: float
  elapsed_time: float = 0.0


@dataclass(frozen=True)
class TimeResolvedFluorescenceResult(FluorescenceResult):
  """Reserved time-resolved result; delay and integration are seconds."""

  delay: float = 0.0
  integration: float = 0.0


@dataclass(frozen=True)
class FluorescencePolarizationResult:
  """Reserved parallel/perpendicular plate data; acquisition is unsupported.

  Wavelengths are in nm, temperature in degrees Celsius, timestamp in Unix
  seconds, and elapsed_time in nominal kinetic seconds. Unselected wells are None.
  """

  parallel_data: PlateData
  perpendicular_data: PlateData
  excitation_wavelength: int
  emission_wavelength: int
  temperature: Optional[float]
  timestamp: float
  elapsed_time: float = 0.0


def empty_plate_data(rows: int, columns: int) -> PlateData:
  """Allocate independent plate rows with no measured values."""
  return [[None for _ in range(columns)] for _ in range(rows)]
