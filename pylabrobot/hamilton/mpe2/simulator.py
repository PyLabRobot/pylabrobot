"""A stand-in for the MPE2's connection, to run protocols and tests without one.

It answers ``MPE2``'s commands as an MPE2 with firmware V1.9.5.619, a 96-port manifold and waste
container 1 does: it follows the firmware's plate states, keeps a command busy
for a few polls so the driver's waiting is exercised, refuses a command in the wrong state the
firmware's way (a HoiError with ISPEControl's InvalidCommandForState), and ends a failed command
with the firmware's error entries in ``GetLastCommandResults``.

Faults can be set up to test recovery: a low or high gas supply (``input_pressure_psi``), an error on
a given command (``inject_error``), a full or missing waste container, and a command that never
answers (``hang_on``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type

from pylabrobot.hamilton.mpe2 import commands as cmd
from pylabrobot.hamilton.mpe2 import errors
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry

S = cmd.SPEControlState

# The objects' addresses, as firmware V1.9.5.619 reports them.
ADDRESSES: Dict[str, Address] = {
  cmd.SPE_CONTROL_PATH: Address(1, 1, 0x1000),
  cmd.WASTE_SYSTEM_PATH: Address(1, 1, 0x0300),
  cmd.VALVES_AND_SENSORS_PATH: Address(1, 1, 0x0200),
  cmd.VACUUM_PATH: Address(1, 1, 0x0310),
  cmd.VACUUM_CALIBRATION_PATH: Address(1, 1, 0x5600),
  cmd.MODULE_INFORMATION_PATH: Address(1, 1, 0x6000),
  # The simulator's own address: the driver finds the real one at setup.
  "SPEMainControllerRoot.MotorControllerRoot.SPEMotorControl": Address(1, 2, 0x0100),
}
_SPE = ADDRESSES[cmd.SPE_CONTROL_PATH]
_WASTE_SYSTEM = ADDRESSES[cmd.WASTE_SYSTEM_PATH]
_VACUUM = ADDRESSES[cmd.VACUUM_PATH]
_MOTOR_CONTROL = ADDRESSES["SPEMainControllerRoot.MotorControllerRoot.SPEMotorControl"]

# A 96-port manifold's per-well limits: flow in standard L/min, pressure in psi.
DEFAULT_RANGES = cmd.ControlPointRanges(
  min_flow_per_well=0.0,
  max_flow_per_well=1.30,
  min_pressure_per_well=0.0,
  max_pressure_per_well=1.0417,
)
DEFAULT_FIRMWARE_VERSION = "Positive Pressure SPE Controller Runtime V1.9.5.619 95600-02 Rev U"


@dataclass
class _Running:
  """A command the simulated firmware is working through."""

  name: str
  passed: List[int]
  end_state: S
  error: Optional[Tuple[Tuple[int, int, int], int, int]] = None  # address, interface id, code
  polls_left: int = 2


@dataclass
class SimulatedWaste:
  """A simulated waste container."""

  present: bool = True
  full: bool = False


class MPE2Simulator:
  """Answers ``MPE2``'s commands as an MPE2 would.

  Args:
    number_of_ports: the manifold's ports, 96 or 24.
    ranges: the per-well control point limits; a 96-port manifold's by default.
    waste_1_present: whether waste container 1 is in place.
    waste_2_present: whether waste container 2 is in place.
    initialized: start initialized and ready, as an MPE2 left on.
    busy_polls: how many times ``GetLastCommandResults`` reports a command still running.

  Attributes:
    sent: every command received, in order.
    input_pressure_psi: the gas supply, psi; below 80 or above 120 makes a process step fail.
    waste: the waste containers, by number.
    hang_on: a command class that is never answered.
  """

  def __init__(
    self,
    number_of_ports: int = 96,
    ranges: Optional[cmd.ControlPointRanges] = None,
    waste_1_present: bool = True,
    waste_2_present: bool = False,
    initialized: bool = False,
    busy_polls: int = 2,
  ):
    self.manifold = cmd.ManifoldInfo(
      serial_number="SIMULATED", calibration_date="", number_of_ports=number_of_ports
    )
    self.ranges = ranges or DEFAULT_RANGES
    self.waste = {1: SimulatedWaste(waste_1_present), 2: SimulatedWaste(waste_2_present)}
    self.state = S.READY if initialized else S.UNINITIALIZED
    self.initialized = initialized
    # A plate in the MPE2 that it lost track of: "clamped" after a gas abort, "on_shuttle" once a
    # full initialization has brought it out. None when it holds
    # none it doesn't know of. take_plates_off() is a person lifting it off.
    self.plate_left: Optional[str] = None
    # Make StopVacuum fail (the pump keeps running), to test what follows.
    self.fail_stop_vacuum = False
    self.labware = cmd.LabwareParameters(0.0, 0.0, 0.0, 0.0, 0.0)
    self.input_pressure_psi = 116.4
    self.vacuum_on = False
    # How long the waste line's vacuum takes to settle; a shorter stabilize timeout faults
    # (VacuumNotStableBeforeTimeout).
    self.vacuum_stable_after = 5.0
    self._vacuum_started: Optional[float] = None
    self._vacuum_timeout: Optional[float] = None
    # A vacuum fault the waste system answers GetVacuumState with until the next start, also
    # after the pump was stopped.
    self.vacuum_fault: Optional[int] = None
    self.selected_waste = cmd.WasteContainer.CONTAINER_1
    self.busy_polls = busy_polls
    self.hang_on: Optional[Type[Any]] = None
    # A command class the simulated link fails on, as a cut cable would.
    self.fail_link_on: Optional[Type[Any]] = None
    self.sent: List[Any] = []
    self.connected = False
    self.setups = 0
    self._running: Optional[_Running] = None
    self._last: Tuple[List[int], List[HcResultEntry]] = ([], [])
    self._injected: Dict[str, Tuple[str, int]] = {}

  # ---------------------------------------------------------------------------
  # Faults
  # ---------------------------------------------------------------------------

  def inject_error(self, command: str, interface: str = "ISPEControl", code: int = 0x0B) -> None:
    """Make the next ``command`` (a class name, "ClampFilterPlate") end with a firmware error.

    Args:
      command: the command's class name.
      interface: the reporting interface, from ``errors.ERRORS`` ("ISPEControl").
      code: its error code (0x0B: IntrusionDetectedCausedAbort).
    """
    errors.ERRORS[(interface, code)]  # A KeyError for an error the firmware doesn't have.
    self._injected[command] = (interface, code)

  # ---------------------------------------------------------------------------
  # The connection
  # ---------------------------------------------------------------------------

  async def setup(self) -> None:
    """Connect to the simulated MPE2."""
    self.connected = True
    self.setups += 1

  async def stop(self) -> None:
    """Disconnect from the simulated MPE2."""
    self.connected = False

  async def resolve_path(self, path: str) -> Address:
    """The address of a simulated firmware object."""
    if path in ADDRESSES:
      return ADDRESSES[path]
    raise KeyError(f"No {path} on the simulated MPE2.")

  def object_names(self) -> Dict[Tuple[int, int, int], str]:
    """The last path part of each simulated address."""
    return {(a.module, a.node, a.object): p.split(".")[-1] for p, a in ADDRESSES.items()}

  async def execute(self, command: Any, *, read_timeout: Optional[float] = None) -> Any:
    """Answer one command as the MPE2 would, keeping it in ``sent``."""
    if not self.connected:
      raise ConnectionError("The simulated MPE2 is not connected.")
    self.sent.append(command)
    command.build(Address(2, 1, 65535), 1)  # Every command must encode.
    if self.hang_on is not None and isinstance(command, self.hang_on):
      await asyncio.sleep(3600)
    if self.fail_link_on is not None and isinstance(command, self.fail_link_on):
      raise ConnectionError("The simulated link failed.")
    return self._answer(command)

  # ---------------------------------------------------------------------------
  # Queries
  # ---------------------------------------------------------------------------

  def _vacuum_running(self) -> bool:
    """Whether the pump runs, setting the stabilize fault once its timeout has passed."""
    if (
      self.vacuum_on
      and self._vacuum_timeout is not None
      and self._vacuum_started is not None
      and self._vacuum_timeout < self.vacuum_stable_after
      and time.monotonic() - self._vacuum_started >= self._vacuum_timeout
    ):
      self.vacuum_fault = 0x07  # The pump goes on running.
      self._vacuum_timeout = None
    return self.vacuum_on

  def _vacuum_state(self) -> bool:
    """The waste system's answer about the pump: a held fault refuses it."""
    running = self._vacuum_running()
    if self.vacuum_fault is not None:
      self._refuse(_VACUUM, "IVacuum", self.vacuum_fault)
    return running

  def _answer(self, command: Any) -> Any:
    """The answer to a query, or None after starting a command."""
    c = cmd
    queries = {
      c.GetManifoldInformation: lambda: self.manifold,
      c.GetControlPointRanges: lambda: self.ranges,
      c.GetMaximumCombinedHeight: lambda: c.MAXIMUM_STACK_HEIGHT,
      c.GetControlState: lambda: self.state,
      # True only when ready: with a plate placed or clamped the MPE2 answers False.
      c.GetIsInitialized: lambda: self.initialized and self.state == S.READY,
      c.GetLabwareParameters: lambda: self.labware,
      c.GetLastCommandResults: self._last_results,
      c.GetVacuumState: self._vacuum_state,
      c.GetVacuumActive: self._vacuum_running,
      c.GetSelectedWasteContainer: lambda: self.selected_waste,
      c.GetWaste1Status: lambda: c.WasteStatus(self.waste[1].present, self.waste[1].full),
      c.GetWaste2Status: lambda: c.WasteStatus(self.waste[2].present, self.waste[2].full),
      c.GetInputPressure: lambda: self.input_pressure_psi,
      c.GetManifoldPressure: lambda: 5.0 if self._processing() else 0.12,
      c.GetHighPressurePathPressure: lambda: 22.0,
      c.GetLowPressurePathPressure: lambda: 0.12,
      c.GetLowPressurePathFlow: lambda: 0.0,
      c.GetDifferentialPressure: lambda: -0.12,
      c.GetWasteVacuum: lambda: -3.2 if self._vacuum_running() else -0.06,
      c.GetModuleVersion: lambda: DEFAULT_FIRMWARE_VERSION,
    }
    # GetWaste2Status subclasses GetWaste1Status: look the exact class up first.
    answer = queries.get(type(command))
    if answer is not None:
      return answer()
    self._command(command)
    return None

  def _processing(self) -> bool:
    """Whether a process step runs."""
    return self._running is not None and self._running.name.startswith("Process")

  def _last_results(self) -> cmd.LastCommandResults:
    """GetLastCommandResults: busy for a few polls, then the command's end."""
    running = self._running
    if running is not None:
      running.polls_left -= 1
      if running.polls_left > 0:
        return cmd.LastCommandResults(True, running.passed[:1], [])
      self._finish(running)
    passed, entries = self._last
    return cmd.LastCommandResults(False, list(passed), list(entries))

  def _finish(self, running: _Running) -> None:
    """End the running command in its end state, with its error entries if it failed."""
    self._running = None
    self.state = running.end_state
    entries: List[HcResultEntry] = []
    if running.error is not None:
      address, interface_id, code = running.error
      entries.append(errors.simulated_result_entry(address, interface_id, code))
    if running.end_state == S.READY and running.name.startswith("Initialize"):
      self.initialized = running.error is None
    self._last = (running.passed, entries)

  # ---------------------------------------------------------------------------
  # Commands
  # ---------------------------------------------------------------------------

  def _refuse(self, address: Address, interface: str, code: int) -> None:
    """Refuse a command the firmware's way, with a HoiError."""
    raise errors.simulated_refusal(
      (address.module, address.node, address.object), 1, interface, code
    )

  def _require(self, *states: S) -> None:
    """Refuse a command unless the MPE2 is in one of the given states."""
    if self.state not in states:
      self._refuse(_SPE, "ISPEControl", 0x01)

  def _start(self, command: Any, end: S, *passed: S, error: Optional[Tuple[str, int]] = None):
    """Accept a long command: it runs for a few polls, then ends in ``end`` (or fails)."""
    name = type(command).__name__
    if self._running is not None:
      self._refuse(_SPE, "ISPEControl", 0x01)
    injected = self._injected.pop(name, None) or error
    failure = None
    if injected is not None:
      interface, code = injected
      address = {
        "IWasteSystem": _WASTE_SYSTEM,
        "IVacuum": _VACUUM,
        "ISPEMotorControl": _MOTOR_CONTROL,
      }.get(interface, _SPE)
      failure = ((address.module, address.node, address.object), 1, code)
      # A failed process step stops where it is: the plate stays clamped.
      if name.startswith("ProcessFilterPlateToWaste"):
        end = S.FILTER_PLATE_CLAMPED
      elif name == "ProcessFilterPlateToCollection":
        end = S.COLLECTION_PLATE_IN_PROCESS_AREA
    self._running = _Running(
      name=name,
      passed=[int(s) for s in passed] + [int(end)],
      end_state=end,
      error=failure,
      polls_left=self.busy_polls,
    )

  def take_plates_off(self) -> None:
    """A person lifts off a plate the MPE2 lost track of (``plate_left``)."""
    self.plate_left = None

  def _gas_abort(self, command: Any, error: Tuple[str, int], *passed: S) -> None:
    """A process step aborted on the gas supply: the plate stays clamped and the MPE2 loses its
    initialization."""
    self._start(command, S.UNINITIALIZED, *passed, error=error)
    assert self._running is not None
    self._running.end_state = S.UNINITIALIZED  # not where a failed step otherwise stops
    self.initialized = False
    self.plate_left = "clamped"

  def _gas_error(self) -> Optional[Tuple[str, int]]:
    """The firmware's gas supply error for the current input pressure, if any."""
    if self.input_pressure_psi < cmd.MINIMUM_INPUT_PRESSURE:
      return ("ISPEControl", 0x10)
    if self.input_pressure_psi > cmd.MAXIMUM_INPUT_PRESSURE:
      return ("ISPEControl", 0x0E)
    return None

  def _command(self, command: Any) -> None:
    """Start a command as the firmware would, or refuse it."""
    c = cmd
    if isinstance(command, (c.Initialize, c.InitializeWithParameters)):
      self._require(S.UNINITIALIZED, S.READY)
      if self.plate_left == "clamped" and command.smart:
        # CollectionPlateDetectedDuringInitialization; nothing moves.
        self._start(command, S.UNINITIALIZED, S.INITIALIZING, error=("ISPEMotorControl", 0x03))
      elif self.plate_left is not None:
        # The full one brings the plate out, then stops on it:
        # FilterPlateDetectedDuringInitialization.
        self.plate_left = "on_shuttle"
        self._start(command, S.UNINITIALIZED, S.INITIALIZING, error=("ISPEMotorControl", 0x02))
      else:
        self._start(command, S.READY, S.INITIALIZING)
    elif isinstance(command, c.FilterPlatePlaced):
      self._require(S.READY)
      self.labware = c.LabwareParameters(
        command.filter_height, command.nozzle_height, 0.0, 0.0, 0.0
      )
      self._start(command, S.FILTER_PLATE_PLACED)
    elif isinstance(command, c.FilterPlateRemoved):
      self._require(S.FILTER_PLATE_PLACED)
      self.labware = c.LabwareParameters(0.0, 0.0, 0.0, 0.0, 0.0)
      self._start(command, S.READY)
    elif isinstance(command, c.ClampFilterPlate):
      self._require(S.FILTER_PLATE_PLACED, S.INTEGRATION_AREA_PLATE_STACK)
      if self.state == S.INTEGRATION_AREA_PLATE_STACK:
        self._start(command, S.COLLECTION_PLATE_PLACED, S.CLAMPING_FILTER_PLATE_2)
      else:
        self._start(command, S.FILTER_PLATE_CLAMPED, S.CLAMPING_FILTER_PLATE_1)
    elif isinstance(command, c.RetrieveFilterPlate):
      if self.state in (S.COLLECTION_PLATE_PLACED, S.COLLECTION_PLATE_IN_PROCESS_AREA):
        self._start(command, S.INTEGRATION_AREA_PLATE_STACK, S.RETRIEVING_FILTER_PLATE_2)
      else:
        self._require(S.FILTER_PLATE_CLAMPED, S.CLAMPING_FILTER_PLATE_1)
        self._start(command, S.FILTER_PLATE_PLACED, S.RETRIEVING_FILTER_PLATE_1)
    elif isinstance(command, c.CollectionPlatePlaced):
      self._require(S.FILTER_PLATE_CLAMPED)
      self.labware = c.LabwareParameters(
        self.labware.filter_plate_height,
        self.labware.filter_plate_nozzle_height,
        command.collection_plate_height,
        command.offset_from_nozzles,
        0.0,
      )
      # Registered only, nothing moves; the retrieve brings the stack.
      self._start(command, S.COLLECTION_PLATE_PLACED)
    elif isinstance(command, c.CollectionPlateRemoved):
      # Refused with the plates stacked; the filter plate stays clamped.
      self._require(S.COLLECTION_PLATE_PLACED)
      self.labware = c.LabwareParameters(
        self.labware.filter_plate_height, self.labware.filter_plate_nozzle_height, 0.0, 0.0, 0.0
      )
      self._start(command, S.FILTER_PLATE_CLAMPED)
    elif isinstance(command, c.ProcessFilterPlateToWaste):
      self._require(S.FILTER_PLATE_PLACED, S.FILTER_PLATE_CLAMPED)
      number = 1 if command.waste_container == c.WasteContainer.CONTAINER_1 else 2
      waste = self.waste[number]
      error = self._gas_error()
      if not waste.present:
        error = ("IWasteSystem", 0x04)
      elif waste.full:
        error = ("IWasteSystem", 0x05)
      if error is not None and error[0] == "ISPEControl":  # the gas supply
        self._gas_abort(command, error, S.CLAMPING_FILTER_PLATE_1, S.PROCESS_FILTER_PLATE_TO_WASTE)
      elif error is not None:
        # The firmware clamps first, then finds it can't go on: the plate stays clamped.
        self._start(command, S.FILTER_PLATE_CLAMPED, S.CLAMPING_FILTER_PLATE_1, error=error)
      else:
        end = (
          S.FILTER_PLATE_PLACED
          if command.return_plate_to_integration_area
          else S.FILTER_PLATE_CLAMPED
        )
        self._start(command, end, S.CLAMPING_FILTER_PLATE_1, S.PROCESS_FILTER_PLATE_TO_WASTE)
    elif isinstance(command, c.ProcessFilterPlateToCollection):
      self._require(S.COLLECTION_PLATE_PLACED, S.INTEGRATION_AREA_PLATE_STACK)
      error = self._gas_error()
      if error is not None:
        self._gas_abort(
          command, error, S.CLAMPING_FILTER_PLATE_2, S.PROCESS_FILTER_PLATE_TO_COLLECTION
        )
      else:
        end = (
          S.INTEGRATION_AREA_PLATE_STACK
          if command.return_plate_to_integration_area
          else S.COLLECTION_PLATE_IN_PROCESS_AREA
        )
        self._start(command, end, S.CLAMPING_FILTER_PLATE_2, S.PROCESS_FILTER_PLATE_TO_COLLECTION)
    elif isinstance(command, c.Abort):
      running = self._running
      # With nothing running the MPE2 takes an abort and does nothing.
      if running is not None:
        # The process stops where it is: a clamped plate stays clamped.
        stop_state = S.FILTER_PLATE_CLAMPED if running.name.startswith("Process") else self.state
        if running.name == "ProcessFilterPlateToCollection":
          stop_state = S.COLLECTION_PLATE_IN_PROCESS_AREA
        running.end_state = stop_state
        running.error = ((_SPE.module, _SPE.node, _SPE.object), 1, 0x05)
        running.polls_left = 1
    elif isinstance(command, (c.StartVacuum, c.StartVacuumFor)):
      if self._vacuum_running():
        self._refuse(_VACUUM, "IVacuum", 0x03)
      number = 1 if self.selected_waste == c.WasteContainer.CONTAINER_1 else 2
      if not self.waste[number].present:
        self._refuse(_WASTE_SYSTEM, "IWasteSystem", 0x04)
      self.vacuum_on = True
      self.vacuum_fault = None
      self._vacuum_started = time.monotonic()
      self._vacuum_timeout = (
        command.time_ms / 1000 if isinstance(command, c.StartVacuumFor) else None
      )
    elif isinstance(command, c.StopVacuum):
      if self.fail_stop_vacuum:
        self._refuse(_VACUUM, "IVacuum", 0x01)
      self.vacuum_on = False
      self._vacuum_timeout = None
    elif isinstance(command, c.SetSelectedWasteContainer):
      self.selected_waste = command.waste_container
    else:
      raise NotImplementedError(f"The simulator doesn't know {type(command).__name__}.")
