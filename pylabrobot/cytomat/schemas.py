from dataclasses import dataclass
from typing import Optional

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .constants import (
    ActionRegister,
    ActionType,
    LoadStatusAtProcessor,
    LoadStatusFrontOfGate,
    SwapStationPosition,
)
from .schema import CytomatPlate

# TODO combine these
CytomatWell = Well

@dataclass(frozen=True)
class OverviewRegisterState:
    transfer_station_occupied: bool
    device_door_open: bool
    automatic_gate_open: bool
    handler_occupied: bool
    error_register_set: bool
    warning_register_set: bool
    ready_bit_set: bool
    busy_bit_set: bool


@dataclass(frozen=True)
class ActionRegisterState:
    target: ActionType
    action: ActionRegister


@dataclass(frozen=True)
class SwapStationState:
    position: SwapStationPosition
    load_status_front_of_gate: LoadStatusFrontOfGate
    load_status_at_processor: LoadStatusAtProcessor


@dataclass(frozen=True)
class SensorStates:
    INIT_SENSOR_HEIGHT_MOTOR: bool
    INIT_SENSOR_CAROUSEL: bool
    SHOVEL_RETRACTED: bool
    SHOVEL_EXTENDED: bool
    SHOVEL_OCCUPIED: bool
    GATE_OPENED: bool
    GATE_CLOSED: bool
    TRANSFER_STATION_OCCUPIED: bool
    TRANSFER_STATION_POSITION_1: bool
    TRANSFER_STATION_POSITION_2: bool
    INNER_DOOR_OPENED: bool
    CAROUSEL_POSITION: bool
    HANDLER_POSITIONED_TOWARDS_STACKER: bool
    HANDLER_POSITIONED_TOWARDS_GATE: bool
    TRANSFER_STATION_SECOND_PLATE_OCCUPIED: bool


@dataclass(frozen=True)
class PlatePair:
    """
    contains the cytomat plate, containing information about the metadata
    and the plr plate, which contains information about the deck position
    """

    cytomat: CytomatPlate
    pylabrobot: Plate

    def __post_init__(self):
        assert (
            self.cytomat.uid == self.pylabrobot.name
        ), f"Plate names do not match: {self.cytomat.uid} != {self.pylabrobot.name}"
        assert (
            self.cytomat.has_lid == self.pylabrobot.has_lid()
        ), f"Plate lid status do not match: {self.cytomat.has_lid} != {self.pylabrobot.has_lid()}"

    @classmethod
    def from_cytomat_plate(cls, cytomat_plate: CytomatPlate) -> "PlatePair":
        return cls(cytomat=cytomat_plate, pylabrobot=cytomat_plate.to_pylabrobot())

    @classmethod
    def from_pylabrobot_plate(cls, pylabrobot_plate: Plate, wells: Optional[list[CytomatWell]] = None) -> "PlatePair":
        wells = (
            [CytomatWell.empty() for w in pylabrobot_plate.children if isinstance(w, Well)] if wells is None else wells
        )
        return cls(cytomat=CytomatPlate.from_pylabrobot(pylabrobot_plate, wells), pylabrobot=pylabrobot_plate)