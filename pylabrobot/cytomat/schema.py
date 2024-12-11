from dataclasses import dataclass
from typing import Optional

# TODO remove pydantic
from pydantic import BaseModel, Field, root_validator, validator

from .config import CYTOMAT_CONFIG
from .constants import CytomatRack, CytomatType
from .schema import CytomatPlate


from pylabrobot.resources.plate import Plate


# TODO: combine these correctly
CytomatPlate = Plate


class Rack(BaseModel):
    rack_index: int
    idx: dict[int, Optional[CytomatPlate]] = Field(default_factory=dict)
    type: CytomatRack

    @validator("rack_index")
    def check_rack_index(cls, value):
        if value < 1:
            raise ValueError("Rack index must be greater than or equal to 1")
        if value > 10:
            raise ValueError("Rack index must be less than or equal to 10")
        return value

    @root_validator  # todo #186 breaks in some pydantic versions
    def check_key_less_than_slots(cls, values):
        idx = values.get("idx")
        num_slots = values.get("type").num_slots if "type" in values else None

        if idx and num_slots is not None:
            for key in idx.keys():
                if key <= 0:
                    raise ValueError(f"Key '{key}' in 'idx' must be greater than 0")

                if key > num_slots:
                    raise ValueError(
                        f"Key '{key}' in 'idx' must be less than the number of slots in 'type' ({num_slots})"
                    )
        return values


class CytomatRackState(BaseModel):
    racks: list[Rack]

    @validator("racks")
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


@dataclass(frozen=True)
class CytomatRelativeLocation:
    rack: int
    slot: int
    model: CytomatType

    def __post_init__(self):
        racks = CYTOMAT_CONFIG[self.model.value]
        assert 0 < self.rack <= len(racks["racks"]), f"Invalid rack number: {self.rack}"
        assert 0 < self.slot <= racks["racks"][self.rack - 1].num_slots, f"Invalid slot number: {self.slot}"

    def to_firmware_string(self) -> str:
        if self.model in [CytomatType.C2C_425]:
            return f"{str(self.rack).zfill(2)} {str(self.slot).zfill(2)}"

        if self.model in [
            CytomatType.C6000,
            CytomatType.C6002,
            CytomatType.C2C_450_SHAKE,
            CytomatType.SWIRLER,
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