import enum
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Literal, Union

try:
  import numpy.typing as npt

  Image = npt.NDArray
except ImportError:
  Image = object  # type: ignore


class Objective(enum.Enum):
  O_40X_PL_APO = enum.auto()
  O_60X_PL_FL = enum.auto()
  O_4X_PL_FL = enum.auto()
  O_20X_PL_FL_Phase = enum.auto()
  O_40X_PL_FL_Phase = enum.auto()
  O_2_5X_PL_ACH_Meiji = enum.auto()
  O_10X_PL_FL_Phase = enum.auto()
  O_1_25X_PL_APO = enum.auto()
  O_10X_PL_FL = enum.auto()
  O_60X_OIL_PL_FL = enum.auto()
  O_4X_PL_ACH = enum.auto()
  O_40X_PL_ACH = enum.auto()
  O_40X_PL_FL = enum.auto()
  O_2X_PL_ACH_Motic = enum.auto()
  O_100X_OIL_PL_FL = enum.auto()
  O_4X_PL_FL_Phase = enum.auto()
  O_20X_PL_FL = enum.auto()
  O_20X_PL_ACH = enum.auto()
  O_2_5X_FL_Zeiss = enum.auto()
  O_100X_OIL_PL_APO = enum.auto()
  O_60X_OIL_PL_APO = enum.auto()
  O_20X_PL_APO = enum.auto()

  @property
  def magnification(self) -> float:
    return {
      Objective.O_40X_PL_APO: 40,
      Objective.O_60X_PL_FL: 60,
      Objective.O_4X_PL_FL: 4,
      Objective.O_20X_PL_FL_Phase: 20,
      Objective.O_40X_PL_FL_Phase: 40,
      Objective.O_2_5X_PL_ACH_Meiji: 2.5,
      Objective.O_10X_PL_FL_Phase: 10,
      Objective.O_1_25X_PL_APO: 1.25,
      Objective.O_10X_PL_FL: 10,
      Objective.O_60X_OIL_PL_FL: 60,
      Objective.O_4X_PL_ACH: 4,
      Objective.O_40X_PL_ACH: 40,
      Objective.O_40X_PL_FL: 40,
      Objective.O_2X_PL_ACH_Motic: 2,
      Objective.O_100X_OIL_PL_FL: 100,
      Objective.O_4X_PL_FL_Phase: 4,
      Objective.O_20X_PL_FL: 20,
      Objective.O_20X_PL_ACH: 20,
      Objective.O_2_5X_FL_Zeiss: 2.5,
      Objective.O_100X_OIL_PL_APO: 100,
      Objective.O_60X_OIL_PL_APO: 60,
      Objective.O_20X_PL_APO: 20,
    }[self]


class ImagingMode(enum.Enum):
  BRIGHTFIELD = enum.auto()
  PHASE_CONTRAST = enum.auto()
  COLOR_BRIGHTFIELD = enum.auto()

  C377_647 = enum.auto()
  C400_647 = enum.auto()
  C469_593 = enum.auto()
  ACRIDINE_ORANGE = enum.auto()
  CFP = enum.auto()
  CFP_FRET_V2 = enum.auto()
  CFP_YFP_FRET = enum.auto()
  CFP_YFP_FRET_V2 = enum.auto()
  CHLOROPHYLL_A = enum.auto()
  CY5 = enum.auto()
  CY5_5 = enum.auto()
  CY7 = enum.auto()
  DAPI = enum.auto()
  GFP = enum.auto()
  GFP_CY5 = enum.auto()
  OXIDIZED_ROGFP2 = enum.auto()
  PROPOIDIUM_IODIDE = enum.auto()
  RFP = enum.auto()
  RFP_CY5 = enum.auto()
  TAG_BFP = enum.auto()
  TEXAS_RED = enum.auto()
  YFP = enum.auto()


class NoPlateError(Exception):
  pass


@dataclass
class AutoExposure:
  evaluate_exposure: Callable[[Image], Awaitable[Literal["higher", "lower", "good"]]]
  max_rounds: int
  low: float
  high: float


Exposure = Union[float, Literal["machine-auto"]]
FocalPosition = Union[float, Literal["machine-auto"]]
Gain = Union[float, Literal["machine-auto"]]


@dataclass
class ImagingResult:
  images: List[Image]
  exposure_time: float
  focal_height: float
