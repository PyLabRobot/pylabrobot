import enum
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Literal, Union

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
  # Cytation objectives
  O_1_25X_PL_APO = enum.auto()
  O_2X_PL_ACH_Motic = enum.auto()
  O_2_5X_PL_ACH_Meiji = enum.auto()
  O_2_5X_FL_Zeiss = enum.auto()
  O_2_5X_N_PLAN = enum.auto()
  O_4X_PL_FL = enum.auto()
  O_4X_PL_ACH = enum.auto()
  O_4X_PL_FL_Phase = enum.auto()
  O_10X_PL_FL = enum.auto()
  O_10X_PL_FL_Phase = enum.auto()
  O_20X_PL_FL = enum.auto()
  O_20X_PL_ACH = enum.auto()
  O_20X_PL_FL_Phase = enum.auto()
  O_20X_PL_APO = enum.auto()
  O_40X_PL_FL = enum.auto()
  O_40X_PL_ACH = enum.auto()
  O_40X_PL_APO = enum.auto()
  O_40X_PL_FL_Phase = enum.auto()
  O_60X_PL_FL = enum.auto()
  O_60X_OIL_PL_FL = enum.auto()
  O_60X_OIL_PL_APO = enum.auto()
  O_100X_OIL_PL_FL = enum.auto()
  O_100X_OIL_PL_APO = enum.auto()

  @property
  def magnification(self) -> float:
    return {
      Objective.O_1_25X_PL_APO: 1.25,
      Objective.O_2X_PL_ACH_Motic: 2,
      Objective.O_2_5X_PL_ACH_Meiji: 2.5,
      Objective.O_2_5X_FL_Zeiss: 2.5,
      Objective.O_2_5X_N_PLAN: 2.5,
      Objective.O_4X_PL_FL: 4,
      Objective.O_4X_PL_ACH: 4,
      Objective.O_4X_PL_FL_Phase: 4,
      Objective.O_10X_PL_FL: 10,
      Objective.O_10X_PL_FL_Phase: 10,
      Objective.O_20X_PL_FL: 20,
      Objective.O_20X_PL_ACH: 20,
      Objective.O_20X_PL_FL_Phase: 20,
      Objective.O_20X_PL_APO: 20,
      Objective.O_40X_PL_FL: 40,
      Objective.O_40X_PL_ACH: 40,
      Objective.O_40X_PL_APO: 40,
      Objective.O_40X_PL_FL_Phase: 40,
      Objective.O_60X_PL_FL: 60,
      Objective.O_60X_OIL_PL_FL: 60,
      Objective.O_60X_OIL_PL_APO: 60,
      Objective.O_100X_OIL_PL_FL: 100,
      Objective.O_100X_OIL_PL_APO: 100,
    }[self]


class ImagingMode(enum.Enum):
  BRIGHTFIELD = enum.auto()
  PHASE_CONTRAST = enum.auto()
  COLOR_BRIGHTFIELD = enum.auto()

  DAPI = enum.auto()
  GFP = enum.auto()
  RFP = enum.auto()
  CFP = enum.auto()
  YFP = enum.auto()
  CY5 = enum.auto()
  CY5_5 = enum.auto()
  CY7 = enum.auto()
  FITC = enum.auto()
  TEXAS_RED = enum.auto()
  PROPIDIUM_IODIDE = enum.auto()
  ACRIDINE_ORANGE = enum.auto()
  TAG_BFP = enum.auto()


Exposure = Union[float, Literal["machine-auto"]]
FocalPosition = Union[float, Literal["machine-auto"]]
Gain = Union[float, Literal["machine-auto"]]


@dataclass
class AutoExposure:
  evaluate_exposure: Callable[[Image], Awaitable[Literal["higher", "lower", "good"]]]
  max_rounds: int
  low: float
  high: float


@dataclass
class AutoFocus:
  evaluate_focus: Callable[[Image], float]
  timeout: float
  low: float
  high: float
  tolerance: float = 0.001  # 1 micron


@dataclass
class ImagingResult:
  images: List[Image]
  exposure_time: float
  focal_height: float
