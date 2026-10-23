"""LiCONiC StoreX STX handling and climate control over the serial PLC protocol."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from asyncio import sleep
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Mapping, Optional, Union

from pylabrobot.events import event_operation, resource_reference
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource, ResourceNotFoundError
from pylabrobot.resources.barcode import Barcode

from .errors import CONTROLLER_ERRORS, HANDLING_ERRORS, StoreXError, StoreXProtocolError
from .models import STOREX_MODELS, StoreXModel
from .racks import StoreXCassette

logger = logging.getLogger(__name__)
StorageSite = Union[PlateHolder, Literal["random", "smallest"]]
BarcodeReader = Callable[[], Awaitable[Barcode]]
_ClimateOption = Literal["temperature", "humidity", "co2", "n2"]


@dataclass(frozen=True)
class _ClimateRegisters:
  """PLC data-memory addresses and raw units per public climate unit."""

  actual: int
  target: int
  scale: int


_CLIMATE_REGISTERS: dict[_ClimateOption, _ClimateRegisters] = {
  "temperature": _ClimateRegisters(actual=982, target=890, scale=10),
  "humidity": _ClimateRegisters(actual=983, target=893, scale=1000),
  "co2": _ClimateRegisters(actual=984, target=894, scale=10000),
  "n2": _ClimateRegisters(actual=985, target=895, scale=10000),
}


class NoFreeSiteError(RuntimeError):
  """No configured free slot has sufficient plate and lid clearance."""


@dataclass(frozen=True)
class UnresolvedPlateTransfer:
  """Last confirmed support and intended destination of an interrupted stage."""

  plate: Plate
  source: PlateHolder
  destination: PlateHolder
  command: str
  error: str


class StoreX(Resource):
  """An STX store owning its cassettes, loading tray, shovel, and serial connection.

  Cassette mapping keys are physical one-based numbers; cassette sites are
  zero-based. Setup opens communication without homing. Call ``initialize()``
  to activate handling. Climate options must match the installed hardware.

  A failed or cancelled plate stage leaves the last confirmed resource assignment
  intact and records ``unresolved_transfer``. Inspect the device and reconcile it
  before further movement. Stopping or reconnecting never clears that record.

  Args:
    name: Device resource name.
    port: Serial port (9600 baud, 8E1, RTS/CTS).
    model: STX model identifier.
    racks: Physical cassette numbers mapped to calibrated cassette resources.
    loading_tray_location: Measured robot-facing tray position, if available.
    has_shaker: Whether a shaker is installed.
    has_humidity_control: Whether adjustable humidity is installed.
    has_co2_control: Whether the DM894/984 gas option is installed.
    has_n2_control: Whether the DM895/985 gas option is installed.
    swap_home_sensor: Calibrated home end switch (100 or 101) for a swap station.
      Omit when absent. Its orientation cannot be inferred from relay 1912.
    read_timeout: Maximum time for a complete command reply, in seconds.
  """

  @staticmethod
  def _validate_timeout(timeout: float) -> None:
    """Reject unbounded or nonpositive waits before issuing commands."""
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and positive")

  def __init__(
    self,
    name: str,
    port: str,
    model: StoreXModel,
    racks: Mapping[int, StoreXCassette],
    *,
    loading_tray_location: Optional[Coordinate] = None,
    has_shaker: bool = False,
    has_humidity_control: bool = False,
    has_co2_control: bool = False,
    has_n2_control: bool = False,
    swap_home_sensor: Optional[Literal[100, 101]] = None,
    read_timeout: float = 1.0,
  ):
    if model not in STOREX_MODELS:
      raise ValueError(f"Unsupported STX model: {model!r}")
    self._validate_timeout(read_timeout)
    if swap_home_sensor not in (None, 100, 101):
      raise ValueError("swap_home_sensor must be 100, 101, or None")
    if any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 250 for n in racks):
      raise ValueError("Cassette numbers must be integers in 1..250")
    if len({id(rack) for rack in racks.values()}) != len(racks):
      raise ValueError("Each physical cassette must have a distinct resource")
    super().__init__(name=name, size_x=0, size_y=0, size_z=0, model=model)
    self._racks = dict(sorted(racks.items()))
    for rack in self._racks.values():
      if not isinstance(rack, StoreXCassette):
        raise TypeError("Use StoreXCassette with explicit pitch calibration")
      self.assign_child_resource(rack, location=None)
    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.shovel = PlateHolder(
      name=f"{name}_shovel", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)
    self.assign_child_resource(self.shovel, location=None)
    self.has_shaker = has_shaker
    self.has_humidity_control = has_humidity_control
    self.has_co2_control = has_co2_control
    self.has_n2_control = has_n2_control
    self.swap_home_sensor = swap_home_sensor
    self.read_timeout = read_timeout
    self.io = Serial(
      human_readable_device_name=f"LiCONiC {model}",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="E",
      stopbits=1,
      rtscts=True,
      timeout=min(0.1, read_timeout),
      write_timeout=read_timeout,
    )
    self._command_lock = asyncio.Lock()
    self._operation_lock = asyncio.Lock()
    self._connected = False
    self._needs_recovery = False
    self._unresolved_transfer: Optional[UnresolvedPlateTransfer] = None

  @property
  def racks(self) -> dict[int, StoreXCassette]:
    """Physical cassette mapping; the contained resources own the inventory."""
    return self._racks.copy()

  @property
  def unresolved_transfer(self) -> Optional[UnresolvedPlateTransfer]:
    """The interrupted plate stage requiring reconciliation, if any."""
    return self._unresolved_transfer

  @property
  def needs_recovery(self) -> bool:
    """Whether a failed operation requires explicit inspection and recovery."""
    return self._needs_recovery

  def serialize(self) -> dict:
    """Refuse to present an incomplete connection or recovery snapshot as restorable."""
    raise NotImplementedError(
      "StoreX serialization is not implemented; retain inventory externally"
    )

  async def _close_transport(self) -> None:
    """Finish closing queued serial I/O before another operation can acquire it."""
    self._connected = False
    task = asyncio.create_task(self.io.stop())
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError:
      await task
      raise

  async def _exchange(self, command: str) -> str:
    """Exchange a frame while holding the command lock, including the opening handshake."""
    try:
      await self.io.write((command + "\r").encode("ascii"))
      deadline = time.monotonic() + self.read_timeout
      response = bytearray()
      while not response.endswith(b"\n"):
        if time.monotonic() >= deadline:
          raise TimeoutError(f"Timed out waiting for {command!r}: {bytes(response)!r}")
        response.extend(await self.io.readline())
        if len(response) > 256:
          raise StoreXProtocolError(f"Oversized response to {command!r}")
      if not response.endswith(b"\r\n") or b"\n" in response[:-1] or b"\r" in response[:-2]:
        raise StoreXProtocolError(f"Invalid CRLF frame for {command!r}: {bytes(response)!r}")
      reply = response[:-2].decode("ascii")
      if not reply:
        raise StoreXProtocolError(f"Empty reply to {command!r}")
      if reply in CONTROLLER_ERRORS:
        raise StoreXError(reply, command, CONTROLLER_ERRORS[reply])
      if command == "CR":
        valid = reply == "CC"
      elif command.startswith(("ST ", "RS ", "WR ")):
        valid = reply == "OK"
      else:
        # RD returns a bit, a 16-bit decimal word, or timer status and values.
        valid = all(part.isascii() and part.isdigit() for part in reply.split(","))
      if not valid:
        raise StoreXProtocolError(f"Unexpected reply to {command!r}: {reply!r}")
      return reply
    except StoreXError:
      raise
    except BaseException:
      try:
        await self._close_transport()
      except Exception:
        logger.exception("Could not close StoreX after an interrupted exchange")
      raise

  async def _send_command(self, command: str) -> str:
    """Serialize one exchange and never replay an uncertain write."""
    async with self._command_lock:
      if not self._connected:
        raise RuntimeError("StoreX is disconnected; call setup()")
      return await self._exchange(command)

  async def _read_bit(self, relay: int) -> bool:
    """Read a relay, rejecting any response other than 0 or 1."""
    reply = await self._send_command(f"RD {relay}")
    if reply not in ("0", "1"):
      raise StoreXProtocolError(f"RD {relay}: expected 0 or 1, got {reply!r}")
    return reply == "1"

  async def _read_word(self, register: int) -> int:
    """Read an unsigned PLC data-memory word."""
    reply = await self._send_command(f"RD DM{register}")
    if not reply.isdigit() or not 0 <= int(reply) <= 65535:
      raise StoreXProtocolError(f"RD DM{register}: invalid 16-bit word {reply!r}")
    return int(reply)

  async def _check_error(self) -> None:
    """Raise the handling error while it is present; leave it available for diagnosis."""
    if await self._read_bit(1814):
      code = f"{await self._read_word(200):05d}"
      raise StoreXError(code, "RD DM200", HANDLING_ERRORS.get(code, "Unknown handling error"))

  async def _wait_ready(self, timeout: float) -> None:
    """Poll faults while busy, with a monotonic deadline."""
    deadline = time.monotonic() + timeout
    while True:
      await self._check_error()
      if await self._read_bit(1915):
        return
      if time.monotonic() >= deadline:
        raise TimeoutError("StoreX handler did not become ready")
      await sleep(0.1)

  async def _send_command_and_wait_until_ready(self, command: str, timeout: float) -> None:
    """Issue one operation and wait past the PLC's startup race before polling."""
    try:
      await self._send_command(command)
      await sleep(0.25)  # V0711 requires >200 ms before first ready query.
      await self._wait_ready(timeout)
    except BaseException:
      self._needs_recovery = True
      logger.exception("StoreX operation %s requires recovery", command)
      raise

  def _require_resolved(self) -> None:
    """Block operations that would obscure an interrupted plate transfer."""
    if self._unresolved_transfer is not None:
      raise RuntimeError(
        "Plate transfer is unresolved; inspect and call resolve_unresolved_transfer()"
      )

  async def _require_operable(self) -> None:
    """Check live handling state and unsupported handshake modes before motion."""
    self._require_resolved()
    if self._needs_recovery:
      raise RuntimeError("StoreX requires inspection and recover() before further motion")
    await self._check_error()
    if not await self._read_bit(1915):
      raise RuntimeError("StoreX handler is busy or uninitialized")
    if not (await self._read_word(202)) & 4:
      raise RuntimeError("Call initialize() before handling plates")
    for relay in (1601, 1610, 1613, 1714, 1910):
      if await self._read_bit(relay):
        raise RuntimeError(f"StoreX mode {relay} requires external sequencing; disable it first")

  async def setup(self) -> None:
    """Open serial communication, preserving inventory and recovery requirements."""
    async with self._operation_lock:
      async with self._command_lock:
        if self._connected:
          return
        logger.warning("StoreX driver is not hardware-verified. Report validation on your model.")
        try:
          await self.io.setup()
          await self.io.send_break(duration=0.2)
          await sleep(0.15)
          await self.io.reset_input_buffer()
          await self.io.reset_output_buffer()
          await self._exchange("CR")
          self._connected = True
        except BaseException:
          await self._close_transport()
          raise
        logger.info("Connected to %s at %s", self.name, self.io.port)

  async def stop(self) -> None:
    """Close communication after the active operation; does not stop physical motion."""
    async with self._operation_lock:
      async with self._command_lock:
        await self._close_transport()
      logger.info("Disconnected %s", self.name)

  async def _read_shovel_sensor(self) -> bool:
    """Enable the HT shovel sensor and allow it to settle before reading."""
    await self._send_command("ST 1911")
    await sleep(0.1)
    return await self._read_bit(1812)

  async def initialize(self, *, timeout: float = 120) -> None:
    """Activate handling when needed, without resetting an existing fault."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      self._require_resolved()
      if self._needs_recovery:
        raise RuntimeError("Inspect the device and call recover()")
      await self._check_error()
      if (await self._read_word(202)) & 4:
        await self._wait_ready(timeout)
        return
      if self.shovel.resource is not None or await self._read_shovel_sensor():
        raise RuntimeError("Cannot initialize with a plate on the shovel")
      await self._send_command_and_wait_until_ready("ST 1801", timeout)
      if not (await self._read_word(202)) & 4:
        self._needs_recovery = True
        raise RuntimeError("StoreX did not confirm initialization")

  async def recover(self, *, timeout: float = 120) -> None:
    """After inspection, clear a handling fault and initialize an empty shovel.

    If already ready and initialized, only clear the local recovery requirement.
    With a plate on the shovel or an unresolved transfer, automatic reset/homing
    is refused; reconcile the plate or use the manufacturer's recovery procedure.
    """
    self._validate_timeout(timeout)
    async with self._operation_lock:
      self._require_resolved()
      error = await self._read_bit(1814)
      if not error and await self._read_bit(1915) and (await self._read_word(202)) & 4:
        if await self._read_bit(1910):
          if self.shovel.resource is not None or await self._read_shovel_sensor():
            raise RuntimeError("Inspect the plate on the shovel before leaving barcode mode")
          await self._send_command_and_wait_until_ready("RS 1910", timeout)
        self._needs_recovery = False
        return
      if self.shovel.resource is not None or await self._read_shovel_sensor():
        raise RuntimeError("Remove or reconcile the plate on the shovel before reset/homing")
      self._needs_recovery = True
      await self._send_command("ST 1900")
      await sleep(0.25)
      await self._send_command_and_wait_until_ready("ST 1801", timeout)
      if not (await self._read_word(202)) & 4:
        raise RuntimeError("StoreX did not confirm initialization after recovery")
      self._needs_recovery = False
      logger.info("Recovered %s", self.name)

  async def request_ready(self) -> bool:
    """Read full handler readiness (1915)."""
    return await self._read_bit(1915)

  async def request_plate_ready(self) -> bool:
    """Read the early transfer handoff flag (1815), distinct from full readiness."""
    return await self._read_bit(1815)

  async def request_error(self) -> Optional[StoreXError]:
    """Read the current fault without acknowledging or clearing it."""
    try:
      await self._check_error()
    except StoreXError as error:
      return error
    return None

  async def check_shovel_sensor(self) -> bool:
    """Read the activated shovel plate sensor."""
    async with self._operation_lock:
      return await self._read_shovel_sensor()

  async def check_transfer_sensor(self) -> bool:
    """Read the primary transfer-station plate sensor."""
    return await self._read_bit(1813)

  async def check_second_transfer_sensor(self) -> bool:
    """Read the optional second transfer-station plate sensor."""
    return await self._read_bit(1807)

  async def wait_for_transfer_station(self, occupied: bool = False, *, timeout: float = 60) -> None:
    """Wait a bounded time for an external robot or operator to change occupancy."""
    self._validate_timeout(timeout)
    deadline = time.monotonic() + timeout
    while await self.check_transfer_sensor() != occupied:
      await self._check_error()
      if time.monotonic() >= deadline:
        raise TimeoutError(f"Transfer station did not become occupied={occupied}")
      await sleep(0.1)

  async def _set_gate(self, opened: bool, timeout: float) -> None:
    """Move to a gate state and verify the corresponding physical end switch."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      await self._require_operable()
      relay = 3 if opened else 2
      if await self._read_bit(relay):
        return
      try:
        await self._send_command_and_wait_until_ready("ST 1901" if opened else "ST 1902", timeout)
        if not await self._read_bit(relay):
          raise RuntimeError("Gate end switch did not confirm the requested position")
      except BaseException:
        self._needs_recovery = True
        raise

  async def open_door(self, *, timeout: float = 60) -> None:
    """Open the robot access gate, confirming the open end switch."""
    await self._set_gate(True, timeout)

  async def close_door(self, *, timeout: float = 60) -> None:
    """Close the robot access gate, confirming the closed end switch."""
    await self._set_gate(False, timeout)

  async def _is_timer_active(self, timer: int) -> bool:
    """Read a debounced swap switch (status,current,preset timer frame)."""
    reply = await self._send_command(f"RD T{timer}")
    parts = reply.split(",")
    if (
      len(parts) != 3
      or parts[0] not in ("0", "1")
      or any(not p.isdigit() or not 0 <= int(p) <= 65535 for p in parts[1:])
    ):
      raise StoreXProtocolError(f"Invalid timer reply: {reply!r}")
    return parts[0] == "1"

  async def request_swap_station_position(self) -> Literal["home", "swapped", "moving"]:
    """Determine position from debounced end switches, never the destination relay."""
    if self.swap_home_sensor is None:
      raise RuntimeError("Configure the measured swap_home_sensor (100 or 101)")
    sensor100 = await self._is_timer_active(17)
    sensor101 = await self._is_timer_active(16)
    if sensor100 and sensor101:
      raise StoreXProtocolError("Both swap station end switches are active")
    home = sensor100 if self.swap_home_sensor == 100 else sensor101
    away = sensor101 if self.swap_home_sensor == 100 else sensor100
    return "home" if home else "swapped" if away else "moving"

  async def turn_swap_station(self, home: bool, *, timeout: float = 30) -> None:
    """Set a swap destination and wait for its debounced physical end switch."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      await self._require_operable()
      target = "home" if home else "swapped"
      position = await self.request_swap_station_position()
      commanded_home = not await self._read_bit(1912)
      if position == target and commanded_home == home:
        return
      try:
        await self._send_command("RS 1912" if home else "ST 1912")
        await sleep(0.3)
        deadline = time.monotonic() + timeout
        while await self.request_swap_station_position() != target:
          await self._check_error()
          if time.monotonic() >= deadline:
            raise TimeoutError(f"Swap station did not reach {target}")
          await sleep(0.1)
      except BaseException:
        self._needs_recovery = True
        raise

  def _locate(self, site: PlateHolder) -> tuple[int, int, StoreXCassette]:
    """Derive physical cassette and level from actual resource membership."""
    for number, rack in self._racks.items():
      if rack.parent is self and site.parent is rack:
        for spot, candidate in rack.sites.items():
          if candidate is site:
            if not 0 <= spot < rack.capacity:
              raise ValueError("Cassette sites must be contiguous and zero-based")
            return number, spot + 1, rack
    raise ValueError(f"{site.name!r} is not a configured cassette slot")

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    """Find a unique plate in the resource tree, including tray and shovel."""
    holders = [self.loading_tray, self.shovel] + [
      site for rack in self._racks.values() for site in rack.sites.values()
    ]
    found = [
      site for site in holders if site.resource is not None and site.resource.name == plate_name
    ]
    if not found:
      raise ResourceNotFoundError(f"Plate {plate_name!r} is not in {self.name!r}")
    if len(found) != 1:
      raise ValueError(f"Plate name {plate_name!r} is ambiguous")
    return found[0]

  def get_num_free_sites(self) -> int:
    """Count unoccupied modeled slots; this is not a physical inventory scan."""
    return sum(len(rack.get_free_sites()) for rack in self._racks.values())

  @staticmethod
  def _get_plate_height(plate: Plate) -> float:
    """Include the actual lid geometry when checking slot clearance."""
    height = plate.get_size_z()
    if plate.lid is not None:
      height = max(height, plate.get_lid_location(plate.lid).z + plate.lid.get_size_z())
    return height

  def _get_available_sites(self, plate: Plate) -> list[PlateHolder]:
    """Return free slots ordered by plate clearance."""
    sites = sorted(
      (
        site
        for rack in self._racks.values()
        for site in rack.get_free_sites()
        if site.get_size_z() >= self._get_plate_height(plate)
      ),
      key=lambda s: s.get_size_z(),
    )
    if not sites:
      raise NoFreeSiteError(
        f"No free slot fits {plate.name!r}, height {self._get_plate_height(plate):g}"
      )
    return sites

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    """Choose the smallest free slot that fits the plate and its lid."""
    return self._get_available_sites(plate)[0]

  def find_random_site(self, plate: Plate) -> PlateHolder:
    """Choose a free slot that fits the plate and its lid."""
    return random.choice(self._get_available_sites(plate))

  async def _select_site(self, site: PlateHolder, timeout: float) -> None:
    """Set cassette calibration and wait for carousel positioning before selecting a level."""
    number, level, rack = self._locate(site)
    await self._send_command(f"WR DM23 {rack.pitch_steps}")
    await self._send_command(f"WR DM25 {rack.capacity}")
    await self._send_command_and_wait_until_ready(f"WR DM0 {number}", timeout)
    await self._send_command(f"WR DM5 {level}")

  async def _require_transfer_configuration(self) -> None:
    """Keep the modeled tray on the primary, home-position transfer route."""
    if await self._read_bit(1607):
      raise RuntimeError("Transfer stations are reversed (1607); restore the primary route first")
    if self.swap_home_sensor is not None and await self.request_swap_station_position() != "home":
      raise RuntimeError("Move the swap station home before a tracked plate transfer")

  async def _require_occupancy(self, holder: PlateHolder, occupied: bool) -> None:
    """Confirm live tray/shovel occupancy; cassette inventory requires operator knowledge."""
    if holder is self.loading_tray:
      actual = await self.check_transfer_sensor()
    elif holder is self.shovel:
      actual = await self._read_shovel_sensor()
    else:
      return
    if actual != occupied:
      raise RuntimeError(f"{holder.name} sensor reports occupied={actual}, expected {occupied}")

  async def _execute_transfer_stage(
    self, plate: Plate, source: PlateHolder, destination: PlateHolder, command: str, timeout: float
  ) -> None:
    """Commit one confirmed physical stage, retaining ambiguity on any interruption."""
    destination.check_can_drop_resource_here(plate, reassign=False)
    await self._require_occupancy(source, True)
    await self._require_occupancy(destination, False)
    try:
      await self._send_command_and_wait_until_ready(command, timeout)
      await self._require_occupancy(source, False)
      await self._require_occupancy(destination, True)
      plate.unassign()
      destination.assign_child_resource(plate)
    except BaseException as error:
      self._needs_recovery = True
      self._unresolved_transfer = UnresolvedPlateTransfer(
        plate, source, destination, command, str(error)
      )
      logger.error(
        "Unresolved plate %s: %s -> %s (%s)", plate.name, source.name, destination.name, command
      )
      raise

  async def _move_plate(
    self, plate: Plate, destination: PlateHolder, timeout: float, event_name: str
  ) -> Plate:
    """Execute a complete transfer with the operation lock held."""
    await self._require_operable()
    await self._require_transfer_configuration()
    source = plate.parent
    if not isinstance(source, PlateHolder):
      raise ValueError("Plate is not assigned to a StoreX holder")
    if source is not self.loading_tray and source is not self.shovel:
      self._locate(source)
    if destination is not self.loading_tray:
      self._locate(destination)
      if self._get_plate_height(plate) > destination.get_size_z():
        raise ValueError("Plate and lid exceed destination slot clearance")
    if source is destination:
      await self._require_occupancy(destination, True)
      return plate
    destination.check_can_drop_resource_here(plate, reassign=False)
    if source is not self.shovel:
      if self.shovel.resource is not None:
        raise RuntimeError("Shovel already has a modeled plate")
      await self._require_occupancy(self.shovel, False)
    await self._require_occupancy(source, True)
    await self._require_occupancy(destination, False)
    with event_operation(
      event_name,
      device=resource_reference(self),
      resources=[resource_reference(plate)],
      source=resource_reference(source),
      destination=resource_reference(destination),
    ):
      if source is not self.shovel:
        selection = destination if source is self.loading_tray else source
        await self._select_site(selection, timeout)
        await self._execute_transfer_stage(
          plate,
          source,
          self.shovel,
          "ST 1907" if source is self.loading_tray else "ST 1908",
          timeout,
        )
      if destination is not self.loading_tray:
        await self._select_site(destination, timeout)
      elif source is self.shovel:
        # Put-to-transfer also requires valid cassette and level registers.
        if not self._racks:
          raise ValueError("Configure a cassette before placing from the shovel")
        await self._select_site(next(iter(self._racks.values())).sites[0], timeout)
      await self._execute_transfer_stage(
        plate,
        self.shovel,
        destination,
        "ST 1906" if destination is self.loading_tray else "ST 1909",
        timeout,
      )
    return plate

  async def fetch_plate_to_loading_tray(
    self, plate: Union[Plate, str], *, timeout: float = 120
  ) -> Plate:
    """Fetch a named or modeled plate; a confirmed plate already on the tray is a no-op."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      if isinstance(plate, str):
        resource = self.get_site_by_plate_name(plate).resource
        if not isinstance(resource, Plate):
          raise ResourceNotFoundError(plate)
        plate = resource
      return await self._move_plate(plate, self.loading_tray, timeout, "incubator.fetch_plate")

  async def store_plate(self, plate: Plate, site: PlateHolder, *, timeout: float = 120) -> Plate:
    """Move a plate to a particular slot, including from another cassette or the shovel."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      self._locate(site)
      return await self._move_plate(plate, site, timeout, "incubator.take_in_plate")

  async def take_in_plate(self, site: StorageSite = "smallest", *, timeout: float = 120) -> Plate:
    """Store the loading-tray plate in an explicit, smallest, or random free slot."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      plate = self.loading_tray.resource
      if not isinstance(plate, Plate):
        raise ResourceNotFoundError("No plate is assigned to the loading tray")
      if site == "smallest":
        destination = self.find_smallest_site_for_plate(plate)
      elif site == "random":
        destination = self.find_random_site(plate)
      elif isinstance(site, PlateHolder):
        destination = site
      else:
        raise ValueError(f"Invalid destination: {site!r}")
      return await self._move_plate(plate, destination, timeout, "incubator.take_in_plate")

  async def resolve_unresolved_transfer(
    self, location: Literal["source", "destination", "unassigned"]
  ) -> Plate:
    """Reconcile an inspected transfer without moving hardware.

    Selecting a cassette slot is explicit operator confirmation of its contents.
    Tray and shovel selections must also agree with their live sensors. An
    unassigned plate must have been removed from both stage endpoints.
    """
    if location not in ("source", "destination", "unassigned"):
      raise ValueError("location must be source, destination, or unassigned")
    async with self._operation_lock:
      transfer = self._unresolved_transfer
      if transfer is None:
        raise RuntimeError("No unresolved transfer")
      await self._check_error()
      if not await self._read_bit(1915):
        raise RuntimeError("Handler must be stationary and ready before reconciliation")
      target = (
        transfer.source
        if location == "source"
        else transfer.destination
        if location == "destination"
        else None
      )
      for holder in (self.shovel, self.loading_tray):
        expected = holder is target or (
          holder is not transfer.source
          and holder is not transfer.destination
          and holder.resource is not None
        )
        await self._require_occupancy(holder, expected)
      if target is not None and transfer.plate.parent is not target:
        target.check_can_drop_resource_here(transfer.plate, reassign=False)
      if transfer.plate.parent is not target:
        transfer.plate.unassign()
        if target is not None:
          target.assign_child_resource(transfer.plate)
      self._unresolved_transfer = None
      self._needs_recovery = False
      logger.info("Reconciled %s to %s", transfer.plate.name, location)
      return transfer.plate

  async def scan_barcode(
    self, site: PlateHolder, read_barcode: BarcodeReader, *, timeout: float = 60
  ) -> Barcode:
    """Position a cassette plate for a caller-owned reader, always resetting BCR mode."""
    self._validate_timeout(timeout)
    async with self._operation_lock:
      self._locate(site)
      plate = site.resource
      if not isinstance(plate, Plate):
        raise ResourceNotFoundError("Assign the plate to its cassette slot before scanning")
      await self._require_operable()
      if self.shovel.resource is not None or await self._read_shovel_sensor():
        raise RuntimeError("Cannot scan with a plate on the shovel")
      await self._select_site(site, timeout)
      try:
        await self._send_command_and_wait_until_ready("ST 1910", timeout)
        barcode = await asyncio.wait_for(read_barcode(), timeout=timeout)
      except BaseException:
        if self._connected:
          try:
            await self._send_command_and_wait_until_ready("RS 1910", timeout)
          except BaseException:
            logger.exception("Could not reset StoreX barcode mode; recovery required")
        raise
      else:
        await self._send_command_and_wait_until_ready("RS 1910", timeout)
        plate.barcode = barcode
        return barcode

  def _require_climate(self, option: _ClimateOption) -> None:
    """Reject absent climate options locally before writing setpoints."""
    if self.model is not None and self.model.endswith("_NC"):
      raise NotImplementedError("This STX model has no climate control")
    installed = {
      "temperature": True,
      "humidity": self.has_humidity_control,
      "co2": self.has_co2_control,
      "n2": self.has_n2_control,
    }
    if not installed[option]:
      raise NotImplementedError(f"Configure the installed {option} control option")

  async def _get_climate(self, option: _ClimateOption, *, target: bool = False) -> float:
    """Decode signed temperature or an unsigned concentration register."""
    self._require_climate(option)
    registers = _CLIMATE_REGISTERS[option]
    value = await self._read_word(registers.target if target else registers.actual)
    if option == "temperature" and value >= 32768:
      value -= 65536
    return value / registers.scale

  async def _set_target_climate(self, option: _ClimateOption, value: float) -> None:
    """Write a validated setpoint and verify its register readback."""
    self._require_climate(option)
    registers = _CLIMATE_REGISTERS[option]
    if not math.isfinite(value):
      raise ValueError("Climate setpoint must be finite")
    if option != "temperature" and not 0 <= value <= 1:
      raise ValueError("Humidity and gas concentrations are fractions in 0..1")
    raw = round(value * registers.scale)
    if option == "temperature" and not -32768 <= raw <= 32767:
      raise ValueError("Temperature is outside the signed 16-bit register range")
    raw &= 65535
    async with self._operation_lock:
      await self._send_command(f"WR DM{registers.target} {raw:05d}")
      if await self._read_word(registers.target) != raw:
        raise RuntimeError(f"DM{registers.target} did not confirm the requested setpoint")

  async def get_temperature(self) -> float:
    """Read actual temperature in degrees Celsius."""
    return await self._get_climate("temperature")

  async def get_target_temperature(self) -> float:
    """Read target temperature in degrees Celsius."""
    return await self._get_climate("temperature", target=True)

  async def set_target_temperature(self, temperature: float) -> None:
    """Set target temperature in degrees Celsius; use the installed model's limits."""
    await self._set_target_climate("temperature", temperature)

  async def get_humidity(self) -> float:
    """Read actual relative humidity as a fraction."""
    return await self._get_climate("humidity")

  async def get_target_humidity(self) -> float:
    """Read target relative humidity as a fraction."""
    return await self._get_climate("humidity", target=True)

  async def set_target_humidity(self, humidity: float) -> None:
    """Set target relative humidity as a fraction."""
    await self._set_target_climate("humidity", humidity)

  async def get_co2_level(self) -> float:
    """Read actual CO2 volume fraction."""
    return await self._get_climate("co2")

  async def get_target_co2_level(self) -> float:
    """Read target CO2 volume fraction."""
    return await self._get_climate("co2", target=True)

  async def set_target_co2_level(self, co2_level: float) -> None:
    """Set target CO2 volume fraction (0.05 means 5%)."""
    await self._set_target_climate("co2", co2_level)

  async def get_n2_level(self) -> float:
    """Read the DM985 gas volume fraction (N2/O2 depends on installed hardware)."""
    return await self._get_climate("n2")

  async def get_target_n2_level(self) -> float:
    """Read the DM895 target gas volume fraction."""
    return await self._get_climate("n2", target=True)

  async def set_target_n2_level(self, n2_level: float) -> None:
    """Set target gas volume fraction for the configured N2/O2 hardware."""
    await self._set_target_climate("n2", n2_level)

  def _require_shaker(self) -> None:
    """Reject shaker access unless that option is configured."""
    if not self.has_shaker:
      raise NotImplementedError("No shaker configured")

  async def is_shaker_enabled(self) -> bool:
    """Read the shaker activation relay; this does not measure physical speed."""
    self._require_shaker()
    return await self._read_bit(1913)

  async def get_shaker_speed(self) -> int:
    """Read the vendor speed setting (1..50), whose physical Hz scale is unverified."""
    self._require_shaker()
    return await self._read_word(39)

  async def start_shaking(self, speed: int) -> None:
    """Set the vendor's integer speed (1..50) directly and activate the shaker."""
    self._require_shaker()
    if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 50:
      raise ValueError("speed must be a vendor setting in 1..50 (not a frequency in Hz)")
    async with self._operation_lock:
      await self._send_command(f"WR DM39 {speed:05d}")
      if await self._read_word(39) != speed:
        raise RuntimeError("Shaker speed setpoint was not accepted")
      if not await self.is_shaker_enabled():
        await self._send_command("ST 1913")
      if not await self.is_shaker_enabled():
        raise RuntimeError("Shaker activation was not confirmed")

  async def stop_shaking(self) -> None:
    """Deactivate shaking and confirm its control relay."""
    self._require_shaker()
    async with self._operation_lock:
      if await self.is_shaker_enabled():
        await self._send_command("RS 1913")
      if await self.is_shaker_enabled():
        raise RuntimeError("Shaker deactivation was not confirmed")
