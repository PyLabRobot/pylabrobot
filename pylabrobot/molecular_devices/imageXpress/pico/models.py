import enum
import sys
from dataclasses import dataclass
from typing import List, Literal, Union

if sys.version_info >= (3, 10):
  from typing import TypeAlias
else:
  from typing_extensions import TypeAlias

try:
  import numpy.typing as npt  # type: ignore

  Image: TypeAlias = npt.NDArray
except ImportError:
  Image: TypeAlias = object  # type: ignore


class Objective(enum.Enum):
  """Objectives available on the ImageXpress Pico."""

  O_2_5X_N_PLAN = enum.auto()
  O_4X_PL_FL = enum.auto()
  O_10X_PL_FL = enum.auto()
  O_20X_PL_FL = enum.auto()
  O_40X_PL_FL = enum.auto()

  @property
  def magnification(self) -> float:
    return {
      Objective.O_2_5X_N_PLAN: 2.5,
      Objective.O_4X_PL_FL: 4,
      Objective.O_10X_PL_FL: 10,
      Objective.O_20X_PL_FL: 20,
      Objective.O_40X_PL_FL: 40,
    }[self]

  @property
  def objective_id(self) -> str:
    """The identifier the instrument uses for this objective."""
    return {
      Objective.O_2_5X_N_PLAN: "N PLAN 2.5x/0.07",
      Objective.O_4X_PL_FL: "PL FLUOTAR 4x/0.13",
      Objective.O_10X_PL_FL: "PL FLUOTAR 10x/0.30",
      Objective.O_20X_PL_FL: "PL FLUOTAR 20x/0.40",
      Objective.O_40X_PL_FL: "PL FLUOTAR 40x/0.60",
    }[self]


class ImagingMode(enum.Enum):
  """Imaging modes available on the ImageXpress Pico."""

  BRIGHTFIELD = enum.auto()

  CY5 = enum.auto()
  DAPI = enum.auto()
  GFP = enum.auto()
  RFP = enum.auto()
  TEXAS_RED = enum.auto()

  @property
  def light_channel(self) -> int:
    return {
      ImagingMode.BRIGHTFIELD: 5,
      ImagingMode.CY5: 0,
      ImagingMode.DAPI: 0,
      ImagingMode.GFP: 0,
      ImagingMode.RFP: 0,
      ImagingMode.TEXAS_RED: 0,
    }[self]

  @property
  def filter_cube(self) -> str:
    """The identifier of the filter cube this mode needs, or "" if it needs none."""
    return {
      ImagingMode.BRIGHTFIELD: "",
      ImagingMode.CY5: "Cy5",
      ImagingMode.DAPI: "DAPI",
      ImagingMode.GFP: "FITC",
      ImagingMode.RFP: "TRITC",
      ImagingMode.TEXAS_RED: "TxRed",
    }[self]

  @property
  def excitation_source(self) -> str:
    return {
      ImagingMode.BRIGHTFIELD: "5069278",
      ImagingMode.CY5: "5050028",
      ImagingMode.DAPI: "GUV3809",
      ImagingMode.GFP: "5050029",
      ImagingMode.RFP: "5050028",
      ImagingMode.TEXAS_RED: "5050028",
    }[self]


# "machine-auto" hands the setting to the instrument: auto-exposure for Exposure,
# hardware autofocus for FocalPosition.
Exposure = Union[float, Literal["machine-auto"]]
FocalPosition = Union[float, Literal["machine-auto"]]
Gain = Union[float, Literal["machine-auto"]]


@dataclass
class ImagingResult:
  """Result of a capture.

  Attributes:
    images: One image per acquired frame.
    exposure_time: Exposure time in ms, as reported by the instrument.
    focal_height: Focal height in mm.
  """

  images: List[Image]
  exposure_time: float
  focal_height: float
