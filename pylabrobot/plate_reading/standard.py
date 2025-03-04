import enum
from typing import List, Literal, Union

Image = List[List[float]]


class Objective(enum.Enum):
  O_4x_PL_FL_PHASE = enum.auto()
  O_20x_PL_FL_PHASE = enum.auto()
  O_40x_PL_FL_PHASE = enum.auto()

  @property
  def magnification(self) -> int:
    return {
      Objective.O_4x_PL_FL_PHASE: 4,
      Objective.O_20x_PL_FL_PHASE: 20,
      Objective.O_40x_PL_FL_PHASE: 40,
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


Exposure = Union[float, Literal["auto"]]
FocalPosition = Union[float, Literal["auto"]]
Gain = Union[float, Literal["auto"]]
