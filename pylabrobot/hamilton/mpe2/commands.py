"""The Hamilton MPE2's firmware commands, as Hamilton's own driver sends them.

The MPE2 (Monitored Pressure Extractor) speaks Hamilton's HOI protocol over TCP (port 2000) or a
USB virtual COM port, the same protocol as the Nimbus and the Prep. Its firmware objects, their
interface ids, method ids and argument types below were read from Hamilton's MPE driver
(``Hamilton.Drivers.MPE.dll``) and its generated firmware proxy
(``Hamilton.Module.SPEMainController.dll``, version 1.9.50202.609), installed with the
"Hamilton MPE2 HSL Driver" (2021). The HSL step each command serves is named in its docstring.

Every command is on interface 1. Commands that change the device use action 3 (COMMAND_REQUEST),
queries use action 0 (STATUS_REQUEST). Times are sent in milliseconds. Flow and pressure in a
control point are per well: Hamilton's driver divides the plate's value by the manifold's number of
ports before sending it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated, Any, ClassVar, List, Tuple, cast

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.messages import (
  HoiParams,
  HoiParamsParser,
  parse_into_struct,
)
from pylabrobot.hamilton.transport.tcp.protocol import HamiltonProtocol, Hoi2Action
from pylabrobot.hamilton.transport.tcp.wire_types import (
  F32,
  U32,
  HcResultEntry,
  PaddedBool,
  Str,
  Struct,
  StructArray,
  decode_fragment,
)
from pylabrobot.hamilton.transport.tcp.wire_types import Enum as WEnum

# The firmware objects, by the names Hamilton's driver looks them up with
# (SPEMainControllerSystem.ModuleRootObjectName and CSPEMainControllerRoot's children).
SPE_CONTROL_PATH = "SPEMainControllerRoot.SPEControl"
WASTE_SYSTEM_PATH = "SPEMainControllerRoot.WasteSystem"
VALVES_AND_SENSORS_PATH = "SPEMainControllerRoot.ValvesAndSensors"
VACUUM_PATH = "SPEMainControllerRoot.Vacuum"
VACUUM_CALIBRATION_PATH = "SPEMainControllerRoot.VacuumCalibration"

# The input gas pressure the firmware works with, in psi (ISPEControl errors 0x0E and 0x10:
# "The input pressure has exceeded 120psi", "The input pressure is below 80psi").
MINIMUM_INPUT_PRESSURE = 80.0
MAXIMUM_INPUT_PRESSURE = 120.0

# What the instrument accepts when nothing else says, from Hamilton.MPE.Configuration.xml: a
# filter plate and collection plate stacked no higher than 114 mm.
MAXIMUM_STACK_HEIGHT = 114.0

# Hamilton's driver uses firmware 1.8's command set when the controller reports a version above it.
SPE_CONTROL_FW_1_8 = (1, 8, 0, 0)


class SPEControlState(IntEnum):
  """Where the MPE2 is in its handling of plates (ISPEControl.SPEControlStatesId)."""

  UNINITIALIZED = 0
  INITIALIZING = 1
  READY = 2
  FILTER_PLATE_PLACED = 3
  CLAMPING_FILTER_PLATE_1 = 4
  FILTER_PLATE_CLAMPED = 5
  PROCESS_FILTER_PLATE_TO_WASTE = 6
  RETRIEVING_FILTER_PLATE_1 = 7
  COLLECTION_PLATE_PLACED = 8
  RETRIEVING_FILTER_PLATE_2 = 9
  INTEGRATION_AREA_PLATE_STACK = 10
  PROCESS_FILTER_PLATE_TO_COLLECTION = 11
  CLAMPING_FILTER_PLATE_2 = 12
  EVAPORATOR_IN_INTEGRATION_AREA = 13
  EVAPORATOR_ENTERING_PROCESS_AREA = 14
  EVAPORATOR_IN_PROCESS_AREA = 15
  PRIMING_READY = 16
  PRIMING_FILTER_PLATE_PLACED = 17
  PRIMING_FILTER_PLATE_CLAMPED = 18
  PRIMING_COLLECTION_PLATE_PLACED = 19
  PRIMING_INTEGRATION_AREA_PLATE_STACK = 20
  DISPENSING_FILTER_PLATE_IN_DISPENSING_AREA = 21
  DISPENSING_COLLECTION_PLATE_IN_DISPENSING_AREA = 22
  COLLECTION_PLATE_IN_PROCESS_AREA = 23
  DISPENSING_INTEGRATION_AREA_PLATE_STACK_IN_DISPENSING_AREA = 24
  DISPENSING_FILTER_PLATE_PLACED = 25
  DISPENSING_COLLECTION_PLATE_PLACED = 26


class ControlPointType(IntEnum):
  """What a control point holds steady (ISPEControl.SPEControlPointType)."""

  FLOW = 0
  PRESSURE = 1
  NONE = 2  # "idle" in Hamilton's control point strings: nothing pushed for the duration.


class WasteContainer(IntEnum):
  """Which waste container the vacuum draws into (WasteContainerSelect)."""

  CONTAINER_1 = 0
  CONTAINER_2 = 1
  NONE = 2


@dataclass(frozen=True)
class ControlPoint:
  """One step of processing a filter plate, per well, as the firmware takes it (SPEControlPoint).

  Args:
    duration: how long the step lasts, in seconds.
    type: whether the step holds a flow or a pressure, or pushes nothing.
    value: the flow per well (standard L/min) or the pressure per well (psig).
  """

  duration: F32
  type: WEnum
  value: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode the fields in the firmware's order: duration, type, value."""
    return params.add(self.duration, F32).add(int(self.type), WEnum).add(self.value, F32)


@dataclass(frozen=True)
class ControlPointRanges:
  """The flow and pressure per well the firmware accepts (SPEControlPointRanges)."""

  min_flow_per_well: F32
  max_flow_per_well: F32
  min_pressure_per_well: F32
  max_pressure_per_well: F32


@dataclass(frozen=True)
class ManifoldInfo:
  """The manifold fitted (ManifoldInfo): 96 or 24 ports, its serial number and calibration date."""

  serial_number: Str
  calibration_date: Str
  number_of_ports: U32


@dataclass(frozen=True)
class LabwareParameters:
  """The plate heights the MPE2 holds (SLabwareParameters), in mm."""

  filter_plate_height: F32
  filter_plate_nozzle_height: F32
  collection_plate_height: F32
  offset: F32
  maximum_penetration: F32


@dataclass(frozen=True)
class WasteStatus:
  """Whether a waste container is in place, and whether it is full (WasteStatus)."""

  present: PaddedBool
  full: PaddedBool


@dataclass(frozen=True)
class LastCommandResults:
  """What the last command did (ISPEControl.GetLastCommandResults).

  Attributes:
    command_being_processed: whether the MPE2 is still running it.
    traversed_states: the states it went through.
    results: the firmware's HoiResult entries; none when the command went well (an initialize
      that reached READY answers six empty arrays).
  """

  command_being_processed: bool
  traversed_states: List[int]
  results: List[HcResultEntry]

  @property
  def errors(self) -> List[HcResultEntry]:
    """The entries that are neither a success nor a warning."""
    return [entry for entry in self.results if not entry.is_success]


# =============================================================================
# Base classes
# =============================================================================


@dataclass(frozen=True)
class MPE2Command(TCPCommand[Any]):
  """A command to one of the MPE2's firmware objects, its address resolved by the driver."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  action_code = Hoi2Action.COMMAND_REQUEST
  firmware_path: ClassVar[str] = SPE_CONTROL_PATH

  def target_path(self) -> str:
    """The firmware object this command goes to; commands addressed by an argument (a source
    bottle's calibration) override it."""
    return self.firmware_path

  def build_parameters(self) -> HoiParams:
    """Encode no parameters, unless the command has some."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> Any:
    """A command answers with nothing but its acceptance."""
    return None


@dataclass(frozen=True)
class MPE2Query(MPE2Command):
  """A query: action 0, one value back."""

  action_code = Hoi2Action.STATUS_REQUEST


# =============================================================================
# Reading answers
# =============================================================================


def split_fragments(data: bytes) -> List[Tuple[int, Any]]:
  """Each data fragment's type and value; a type PLR has no decoder for keeps its raw bytes.

  A HoiResult (type 44) has no decoder in PLR yet, so the last command's errors come back raw
  until one is seen from the device.
  """
  out: List[Tuple[int, Any]] = []
  offset = 0
  while offset + 4 <= len(data):
    type_id = data[offset]
    length = int.from_bytes(data[offset + 2 : offset + 4], "little")
    payload = data[offset + 4 : offset + 4 + length]
    try:
      value: Any = decode_fragment(type_id, payload)
    except ValueError:
      value = payload
    out.append((type_id, value))
    offset += 4 + length
  return out


def decode_hoi_result(data: bytes) -> List[HcResultEntry]:
  """A HoiResult structure: six parallel arrays (module, node and object ids, interface id,
  action id, result code), one entry per result. The MPE2 sends them as u16, u16, u16, u8, u16,
  u16 arrays."""
  arrays = [value for _type_id, value in split_fragments(data)]
  if len(arrays) != 6:
    raise ValueError(f"A HoiResult has six arrays, not {len(arrays)}: {data.hex(' ')}")
  return [HcResultEntry(*map(int, entry)) for entry in zip(*arrays)]


def _single(data: bytes, cls: type) -> Any:
  """The one value of an answer, parsed as ``cls``."""
  return parse_into_struct(HoiParamsParser(data), cls).value


def _first_value(data: bytes) -> Any:
  """The first value of an answer, as the wire types decode it."""
  fragments = split_fragments(data)
  if not fragments:
    raise ValueError("The MPE2 answered a query with no value.")
  return fragments[0][1]


# =============================================================================
# SPEControl: plates and processing
# =============================================================================


@dataclass(frozen=True)
class Initialize(MPE2Command):
  """Initialize(bool smart) (method 18): what HSL's Initialize sends when no waste container is
  in."""

  command_id = 18
  smart: bool = True

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(self.smart, PaddedBool)


@dataclass(frozen=True)
class InitializeWithParameters(MPE2Command):
  """Initialize(smart, vacuumRunTime, wasteContainer, disableUpperLimitCheck) (method 22): HSL's
  InitializeWithParameters."""

  command_id = 22
  smart: bool
  vacuum_run_time_ms: int
  waste_container: WasteContainer
  disable_upper_limit_check: bool

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .add(self.smart, PaddedBool)
      .add(self.vacuum_run_time_ms, U32)
      .add(int(self.waste_container), WEnum)
      .add(self.disable_upper_limit_check, PaddedBool)
    )


@dataclass(frozen=True)
class FilterPlatePlaced(MPE2Command):
  """FilterPlatePlaced(filterHeight, nozzleHeight) (method 7), in mm: HSL's FilterPlatePlaced."""

  command_id = 7
  filter_height: float
  nozzle_height: float

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(self.filter_height, F32).add(self.nozzle_height, F32)


@dataclass(frozen=True)
class FilterPlateRemoved(MPE2Command):
  """FilterPlateRemoved (method 8): HSL's FilterPlateRemoved."""

  command_id = 8


@dataclass(frozen=True)
class ClampFilterPlate(MPE2Command):
  """ClampFilterPlate (method 9): HSL's ClampFilterPlate."""

  command_id = 9


@dataclass(frozen=True)
class RetrieveFilterPlate(MPE2Command):
  """RetrieveFilterPlate (method 10): HSL's RetrieveFilterPlate."""

  command_id = 10


@dataclass(frozen=True)
class CollectionPlatePlaced(MPE2Command):
  """CollectionPlatePlaced(collectionPlateHeight, offsetFromNozzles) (method 11), in mm: HSL's
  CollectionPlatePlaced."""

  command_id = 11
  collection_plate_height: float
  offset_from_nozzles: float

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(self.collection_plate_height, F32).add(self.offset_from_nozzles, F32)


@dataclass(frozen=True)
class CollectionPlateRemoved(MPE2Command):
  """CollectionPlateRemoved (method 12): HSL's CollectionPlateRemoved."""

  command_id = 12


@dataclass(frozen=True)
class ProcessFilterPlateToCollection(MPE2Command):
  """ProcessFilterPlateToCollection(controlPoints, returnFilterPlateToIntegrationArea) (method 14):
  HSL's ProcessFilterToCollectionPlate."""

  command_id = 14
  control_points: tuple
  return_plate_to_integration_area: bool

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .add(list(self.control_points), StructArray())
      .add(self.return_plate_to_integration_area, PaddedBool)
    )


@dataclass(frozen=True)
class ProcessFilterPlateToWaste(MPE2Command):
  """ProcessFilterPlateToWaste(controlPoints, returnFilterPlateToIntegrationArea, wasteContainer,
  disableUpperLimitCheck) (method 23): HSL's ProcessFilterToWasteContainer."""

  command_id = 23
  control_points: tuple
  return_plate_to_integration_area: bool
  waste_container: WasteContainer
  disable_upper_limit_check: bool

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .add(list(self.control_points), StructArray())
      .add(self.return_plate_to_integration_area, PaddedBool)
      .add(int(self.waste_container), WEnum)
      .add(self.disable_upper_limit_check, PaddedBool)
    )


@dataclass(frozen=True)
class Abort(MPE2Command):
  """Abort (method 15): HSL's Abort."""

  command_id = 15


@dataclass(frozen=True)
class GetControlState(MPE2Query):
  """ControlState (method 1)."""

  command_id = 1

  @dataclass(frozen=True)
  class Response:
    value: WEnum

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> SPEControlState:
    return SPEControlState(_single(data, cls.Response))


@dataclass(frozen=True)
class GetLabwareParameters(MPE2Query):
  """LabwareParameters (method 2)."""

  command_id = 2

  @dataclass(frozen=True)
  class Response:
    value: Annotated[LabwareParameters, Struct()]

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> LabwareParameters:
    return cast(LabwareParameters, _single(data, cls.Response))


@dataclass(frozen=True)
class GetMaximumCombinedHeight(MPE2Query):
  """MaximumCombinedHeight (method 3): the highest filter and collection plate stack, in mm."""

  command_id = 3

  @dataclass(frozen=True)
  class Response:
    value: F32

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> float:
    return float(_single(data, cls.Response))


@dataclass(frozen=True)
class GetControlPointRanges(MPE2Query):
  """SPEControlPointRange (method 4)."""

  command_id = 4

  @dataclass(frozen=True)
  class Response:
    value: Annotated[ControlPointRanges, Struct()]

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> ControlPointRanges:
    return cast(ControlPointRanges, _single(data, cls.Response))


@dataclass(frozen=True)
class GetManifoldInformation(MPE2Query):
  """ManifoldInformation (method 5)."""

  command_id = 5

  @dataclass(frozen=True)
  class Response:
    value: Annotated[ManifoldInfo, Struct()]

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> ManifoldInfo:
    return cast(ManifoldInfo, _single(data, cls.Response))


@dataclass(frozen=True)
class GetLastCommandResults(MPE2Query):
  """GetLastCommandResults (method 16): whether a command still runs, and how the last one went."""

  command_id = 16

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> LastCommandResults:
    fragments = split_fragments(data)
    busy = bool(fragments[0][1]) if fragments else False
    states = list(fragments[1][1]) if len(fragments) > 1 else []
    results: List[HcResultEntry] = []
    if len(fragments) > 2 and isinstance(fragments[2][1], (bytes, bytearray)):
      results = decode_hoi_result(bytes(fragments[2][1]))
    return LastCommandResults(
      command_being_processed=busy, traversed_states=states, results=results
    )


@dataclass(frozen=True)
class GetIsInitialized(MPE2Query):
  """IsInitialized (method 17)."""

  command_id = 17

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> bool:
    return bool(_single(data, cls.Response))


# =============================================================================
# WasteSystem: the vacuum pump and waste containers
# =============================================================================


@dataclass(frozen=True)
class StopVacuum(MPE2Command):
  """StopVacuum (method 2): HSL's StopVacuum."""

  command_id = 2
  firmware_path = WASTE_SYSTEM_PATH


@dataclass(frozen=True)
class SetSelectedWasteContainer(MPE2Command):
  """SelectedWasteContainer = value (method 3): HSL's StartMPEVacuum sends it first."""

  command_id = 3
  firmware_path = WASTE_SYSTEM_PATH
  waste_container: WasteContainer

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(int(self.waste_container), WEnum)


@dataclass(frozen=True)
class StartVacuumFor(MPE2Command):
  """StartVacuum(time, disableUpperLimitCheck) (method 11), time in ms: HSL's StartVacuum and
  StartMPEVacuum with a run time."""

  command_id = 11
  firmware_path = WASTE_SYSTEM_PATH
  time_ms: int
  disable_upper_limit_check: bool

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(self.time_ms, U32).add(self.disable_upper_limit_check, PaddedBool)


@dataclass(frozen=True)
class StartVacuum(MPE2Command):
  """StartVacuum(disableUpperLimitCheck) (method 12): the pump on until stopped."""

  command_id = 12
  firmware_path = WASTE_SYSTEM_PATH
  disable_upper_limit_check: bool

  def build_parameters(self) -> HoiParams:
    return HoiParams().add(self.disable_upper_limit_check, PaddedBool)


@dataclass(frozen=True)
class GetSelectedWasteContainer(MPE2Query):
  """SelectedWasteContainer (method 4)."""

  command_id = 4
  firmware_path = WASTE_SYSTEM_PATH

  @dataclass(frozen=True)
  class Response:
    value: WEnum

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> WasteContainer:
    return WasteContainer(_single(data, cls.Response))


@dataclass(frozen=True)
class GetWaste1Status(MPE2Query):
  """Waste1Status (method 8)."""

  command_id = 8
  firmware_path = WASTE_SYSTEM_PATH

  @dataclass(frozen=True)
  class Response:
    value: Annotated[WasteStatus, Struct()]

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> WasteStatus:
    return cast(WasteStatus, _single(data, cls.Response))


@dataclass(frozen=True)
class GetWaste2Status(GetWaste1Status):
  """Waste2Status (method 9)."""

  command_id = 9


@dataclass(frozen=True)
class GetVacuumState(MPE2Query):
  """VacuumState (method 10): whether the pump runs."""

  command_id = 10
  firmware_path = WASTE_SYSTEM_PATH

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> bool:
    return bool(_single(data, cls.Response))


# =============================================================================
# ValvesAndSensors and Vacuum: readings only
# =============================================================================


@dataclass(frozen=True)
class _FloatReading(MPE2Query):
  """A query answering one f32 (all the MPE2's pressures and flows are f32)."""

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> float:
    return float(_first_value(data))


@dataclass(frozen=True)
class _BoolReading(MPE2Query):
  """A query answering one bool."""

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> bool:
    return bool(_first_value(data))


@dataclass(frozen=True)
class GetLowPressurePathPressure(_FloatReading):
  """LowPressurePathPressureSensor (method 4), psi."""

  command_id = 4
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetLowPressurePathFlow(_FloatReading):
  """LowPressurePathFlowSensor (method 5), standard L/min."""

  command_id = 5
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetHighPressurePathPressure(_FloatReading):
  """HighPressurePathPressureSensor (method 6), psi."""

  command_id = 6
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetDifferentialPressure(_FloatReading):
  """DifferentialPressureSensor (method 7), psi."""

  command_id = 7
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetInputPressure(_FloatReading):
  """InputPressureSensor (method 8): the gas supply, psi."""

  command_id = 8
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetManifoldPressure(_FloatReading):
  """ManifoldPressure (method 17), psi."""

  command_id = 17
  firmware_path = VALVES_AND_SENSORS_PATH


@dataclass(frozen=True)
class GetVacuumActive(_BoolReading):
  """IVacuum.IsVacuumActive (method 3)."""

  command_id = 3
  firmware_path = VACUUM_PATH


@dataclass(frozen=True)
class GetWasteVacuum(_FloatReading):
  """IVacuumCalibration.GetPressure (method 4): the vacuum in the waste line, psi (negative when
  drawing; about 0 with the pump off)."""

  command_id = 4
  firmware_path = VACUUM_CALIBRATION_PATH


# =============================================================================
# ModuleInformation: the firmware's version
# =============================================================================

MODULE_INFORMATION_PATH = "SPEMainControllerRoot.ModuleInformation"


@dataclass(frozen=True)
class GetModuleVersion(MPE2Query):
  """IModuleInformation.GetModuleVersion (interface 3, method 8), such as "Positive Pressure SPE
  Controller Runtime V1.9.5.619 95600-02 Rev U"."""

  interface_id = 3
  command_id = 8
  firmware_path = MODULE_INFORMATION_PATH

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> str:
    return str(_first_value(data))
