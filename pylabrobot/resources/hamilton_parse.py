from typing import Tuple, Optional

from pylabrobot.resources import MFXCarrier, Plate, PlateCarrier, TipCarrier, TipRack

__all__ = [
  "create_plate",
  "create_tip_rack",
  "create_plate_carrier",
  "create_tip_carrier",
  "create_flex_carrier",
]


def get_resource_type(filepath) -> str:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_plate_for_writing(
  filepath: str,
  ctr_filepath: Optional[str] = None
) -> Tuple[Plate, Optional[str], Optional[str]]:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_tip_rack_for_writing(filepath: str) -> Tuple[TipRack, Optional[str]]:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_plate_carrier_for_writing(filepath: str) -> Tuple[PlateCarrier, Optional[str]]:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_tip_carrier_for_writing(filepath: str) -> Tuple[TipCarrier, Optional[str]]:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_flex_carrier_for_writing(filepath: str) -> Tuple[MFXCarrier, Optional[str]]:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_plate(filepath: str, name: str, ctr_filepath: Optional[str] = None) -> Plate:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_tip_rack(filepath: str, name: str) -> TipRack:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_plate_carrier(filepath: str, name: str) -> PlateCarrier:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_tip_carrier(filepath: str, name: str) -> TipCarrier:
  raise NotImplementedError("hamilton_parse is deprecated")

def create_flex_carrier(filepath: str, name: str) -> MFXCarrier:
  raise NotImplementedError("hamilton_parse is deprecated")
