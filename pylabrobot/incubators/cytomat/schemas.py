from dataclasses import dataclass

from pylabrobot.incubators.cytomat.constants import (
  ActionRegister,
  ActionType,
  LoadStatusAtProcessor,
  LoadStatusFrontOfGate,
  OverviewRegister,
  SwapStationPosition,
)
from pylabrobot.incubators.cytomat.utils import hex_to_binary


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

  @classmethod
  def from_resp(self, resp) -> "OverviewRegisterState":
    binary_value = hex_to_binary(resp)
    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )


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
