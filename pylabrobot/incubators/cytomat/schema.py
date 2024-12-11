import dataclasses
from dataclasses import dataclass
from typing import Optional

from pylabrobot.incubators.cytomat.config import CYTOMAT_CONFIG
from pylabrobot.incubators.cytomat.constants import CytomatRack, CytomatType
from pylabrobot.resources.plate import Plate

# TODO: combine these correctly
CytomatPlate = Plate


# @dataclass(frozen=True)
class Rack:
  rack_index: int
  type: CytomatRack
  idx: dict[int, Optional[CytomatPlate]] = dataclasses.field(default_factory=dict)

  def __init__(self, rack_index: int, type: CytomatRack, idx: dict[int, Optional[CytomatPlate]]):
    self.rack_index = rack_index
    self.type = CytomatRack(**type)
    self.idx = idx

  def dict(self):
    print(self.type)

    def s(p):
      if p is None:
        return None
      if isinstance(p, dict):
        return p
      return p.serialize()

    return {
      "rack_index": self.rack_index,
      "type": dataclasses.asdict(self.type) if not isinstance(self.type, dict) else self.type,
      "idx": {i: s(plate) for i, plate in self.idx.items()},
    }


class CytomatRackState:
  racks: list[Rack]

  def __init__(self, racks: dict):
    self.racks = [Rack(**rack) for rack in racks]

  def check_unique_plate_uid(cls, attr: list[Rack]) -> list[Rack]:
    seen = set()
    for rack in attr:
      for plate in rack.idx.values():
        if plate is None:
          continue
        if plate.uid in seen:
          raise ValueError(f"Duplicate rack index found: {plate.uid}")

        seen.add(rack.rack_index)
    return attr

  @classmethod
  def from_cytomat_type(cls, cytomate_type: CytomatType):
    rack_cfg = CYTOMAT_CONFIG[cytomate_type.value]["racks"]

    rack_state = {
      "racks": [
        {
          "rack_index": rack_index + 1,
          "type": rack,
          "idx": {idx + 1: None for idx in range(rack.num_slots)},
        }
        for rack_index, rack in enumerate(rack_cfg)
      ]
    }

    return cls(**rack_state)

  def dict(self):
    print(self.racks)
    return {"racks": [rack.dict() for rack in self.racks]}

  def json(self):
    import json

    return json.dumps(self.dict(), indent=2)


@dataclass(frozen=True)
class CytomatRelativeLocation:
  rack: int
  slot: int
  model: CytomatType

  def __post_init__(self):
    racks = CYTOMAT_CONFIG[self.model.value]
    assert 0 < self.rack <= len(racks["racks"]), f"Invalid rack number: {self.rack}"
    assert (
      0 < self.slot <= racks["racks"][self.rack - 1].num_slots
    ), f"Invalid slot number: {self.slot}"

  def to_firmware_string(self) -> str:
    if self.model in [CytomatType.C2C_425]:
      return f"{str(self.rack).zfill(2)} {str(self.slot).zfill(2)}"

    if self.model in [
      CytomatType.C6000,
      CytomatType.C6002,
      CytomatType.C2C_450_SHAKE,
      CytomatType.C5C,
    ]:
      racks = CYTOMAT_CONFIG[self.model.value]
      slots_to_skip = sum(r.num_slots for r in racks["racks"][: self.rack - 1])
      if self.model == CytomatType.C2C_450_SHAKE:
        # This is the "rack shaker" we ripped out ever other level so multiply by two.
        # The initial rack shaker is unused, so add fifteen.
        absolute_slot = 15 + 2 * (slots_to_skip + self.slot)
      else:
        absolute_slot = slots_to_skip + self.slot

      return f"{absolute_slot:03}"

    raise ValueError(f"Unsupported Cytomat model: {self.model}")
