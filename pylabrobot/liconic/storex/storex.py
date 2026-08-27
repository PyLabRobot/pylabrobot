"""LiCONiC StoreX driver."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import List, Literal, Optional, Protocol, Sequence, Tuple, Union, cast

from pylabrobot.io.serial import Serial
from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceNotFoundError,
  Rotation,
)
from pylabrobot.resources.barcode import Barcode
from pylabrobot.serializer import deserialize, serialize

from .constants import STOREX_MODELS, ControllerError, HandlingError, StoreXModel
from .errors import controller_error_map, handler_error_map

logger = logging.getLogger(__name__)


STOREX_SITE_HEIGHT_TO_STEPS = {
  5: 377,
  11: 582,
  12: 617,
  17: 788,
  22: 959,
  23: 994,
  24: 1028,
  27: 1131,
  44: 1713,
  53: 2021,
  66: 2467,
  104: 3563,
}

StorageSite = Union[PlateHolder, Literal["random", "smallest"]]


class BarcodeScanner(Protocol):
  """Interface required from an internal barcode scanner."""

  async def setup(self) -> None: ...

  async def stop(self) -> None: ...

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]: ...


class NoFreeSiteError(Exception):
  """Raised when none of the configured rack sites can hold a plate."""


class StoreX(Resource):
  """LiCONiC StoreX automated incubator and storage system (STX line).

  The device contains one or more cassettes represented by
  :class:`~pylabrobot.resources.PlateCarrier` objects and a loading tray. Plate
  transfer, climate, shaking, sensor, and optional barcode operations are sent
  to the controller over its ASCII PLC protocol.

  Serial communication uses 9600 baud, 8 data bits, even parity, 1 stop bit,
  RTS/CTS, and a ``\\r`` command terminator.

  """

  serial_message_encoding = "ascii"

  def __init__(
    self,
    name: str,
    model: StoreXModel,
    port: str,
    racks: Sequence[PlateCarrier],
    loading_tray_location: Coordinate,
    has_shaker: bool = False,
    barcode_scanner: Optional[BarcodeScanner] = None,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
    rotation: Optional[Rotation] = None,
  ) -> None:
    if model not in STOREX_MODELS:
      raise ValueError(f"Unsupported StoreX model: {model!r}")

    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category="incubator",
      model=model,
    )
    self.storex_model = model
    self.has_shaker = has_shaker
    self.barcode_scanner = barcode_scanner
    self._racks = list(racks)

    self.loading_tray = PlateHolder(
      name=f"{name}_tray",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.io = Serial(
      human_readable_device_name=f"LiCONiC StoreX {model}",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="E",
      stopbits=1,
      write_timeout=1,
      timeout=1,
      rtscts=True,
    )

  @property
  def racks(self) -> List[PlateCarrier]:
    """The cassettes in controller order."""
    return self._racks

  @property
  def climate_suffix(self) -> str:
    return self.storex_model.rsplit("_", 1)[-1]

  @property
  def supports_temperature_control(self) -> bool:
    return self.climate_suffix != "NC"

  @property
  def supports_humidity_control(self) -> bool:
    return self.climate_suffix in {"DC2", "DR2", "AR", "DH"}

  @property
  def supports_active_cooling(self) -> bool:
    return self.climate_suffix in {"HC", "HR", "DF"}

  def serialize(self) -> dict:
    return {
      **Resource.serialize(self),
      "port": self.io.port,
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
      "has_shaker": self.has_shaker,
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> "StoreX":
    return cls(
      name=data["name"],
      model=cast(StoreXModel, data["model"]),
      port=data["port"],
      racks=[
        cast(
          PlateCarrier,
          PlateCarrier.deserialize(rack, allow_marshal=allow_marshal),
        )
        for rack in data["racks"]
      ],
      loading_tray_location=cast(
        Coordinate,
        deserialize(data["loading_tray_location"], allow_marshal=allow_marshal),
      ),
      has_shaker=data.get("has_shaker", False),
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      rotation=cast(
        Optional[Rotation],
        deserialize(data.get("rotation"), allow_marshal=allow_marshal),
      ),
    )

  async def setup(self) -> None:
    await self.io.setup()
    try:
      await self.io.send_break(duration=0.2)
      await asyncio.sleep(0.15)
      await self.io.reset_input_buffer()
      await self.io.reset_output_buffer()

      await self.io.write(b"CR\r")
      deadline = time.monotonic() + 1.0
      while time.monotonic() < deadline:
        if (await self.io.readline()).strip() == b"CC":
          break
      else:
        raise TimeoutError("No CC response from StoreX PLC within 1.0 seconds")

      await self.io.write(b"ST 1801\r")
      response = await self.io.readline()
      if response.strip() != b"OK":
        raise RuntimeError(f"Unexpected reply to ST 1801: {response!r}")

      deadline = time.monotonic() + 15.0
      while time.monotonic() < deadline:
        await self.io.write(b"RD 1915\r")
        if (await self.io.readline()).strip() == b"1":
          break
        await asyncio.sleep(0.2)
      else:
        raise TimeoutError("PLC did not signal ready within 15.0 seconds")

      if self.barcode_scanner is not None:
        await self.barcode_scanner.setup()
    except Exception:
      await self.io.stop()
      raise

    logger.info("[StoreX %s] connected: model=%s", self.io.port, self.storex_model)

  async def stop(self) -> None:
    try:
      await self.io.stop()
    finally:
      if self.barcode_scanner is not None:
        await self.barcode_scanner.stop()
    logger.info("[StoreX %s] disconnected", self.io.port)

  async def _send_command(self, command: str) -> str:
    command = command.strip()
    logger.debug("[StoreX %s] send: %s", self.io.port, command)
    await self.io.write(f"{command}\r".encode(self.serial_message_encoding))
    response = (await self.io.read(128)).decode(self.serial_message_encoding).strip()
    if not response:
      raise RuntimeError(f"No response from StoreX PLC for command {command!r}")
    if response.startswith("E"):
      logger.error("[StoreX %s] command %s failed: %s", self.io.port, command, response)
      try:
        error = ControllerError(response)
      except ValueError:
        raise RuntimeError(f"Unknown error {response} when sending command {command}")
      error_type, message = controller_error_map[error]
      raise error_type(message)
    logger.debug("[StoreX %s] recv: %s", self.io.port, response)
    return response

  async def _wait_ready(self, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      if await self._send_command("RD 1915") == "1":
        return
      await asyncio.sleep(0.1)

    if await self._send_command("RD 1814") == "1":
      response = await self._send_command("RD DM200")
      try:
        error = HandlingError(response)
      except ValueError:
        raise RuntimeError(
          f"StoreX handler is in an unknown error state with memory showing {response}"
        )
      error_type, message = handler_error_map[error]
      raise error_type(message)
    raise TimeoutError(f"Incubator did not become ready within {timeout} seconds")

  def _site_to_address(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    if not isinstance(rack, PlateCarrier) or rack not in self._racks:
      raise ValueError(f"Site {site.name!r} is not in a configured StoreX rack")
    try:
      site_index = next(index for index, candidate in rack.sites.items() if candidate is site)
    except StopIteration:
      raise ValueError(f"Site {site.name!r} is not indexed by its parent rack")
    return self._racks.index(rack) + 1, site_index + 1

  def _carrier_to_steps_and_positions(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    if not isinstance(rack, PlateCarrier) or rack not in self._racks:
      raise ValueError(f"Site {site.name!r} is not in a configured StoreX rack")
    if rack.model is None or not rack.model.startswith("storex_rack_"):
      raise ValueError(f"Plate carrier model {rack.model!r} is not compatible with StoreX")
    match = re.fullmatch(r"storex_rack_(\d+)mm_(\d+)", rack.model)
    if match is None:
      raise ValueError(f"Could not parse StoreX plate carrier model: {rack.model}")
    site_height, num_positions = (int(value) for value in match.groups())
    try:
      steps = STOREX_SITE_HEIGHT_TO_STEPS[site_height]
    except KeyError:
      raise ValueError(f"Unknown StoreX site height: {site_height} mm")
    if num_positions != len(rack.sites):
      raise ValueError(
        f"Rack model declares {num_positions} positions but contains {len(rack.sites)} sites"
      )
    return steps, num_positions

  async def _configure_site(self, site: PlateHolder) -> Tuple[int, int]:
    cassette, position = self._site_to_address(site)
    steps, num_positions = self._carrier_to_steps_and_positions(site)
    await self._send_command(f"WR DM0 {cassette}")
    await self._send_command(f"WR DM23 {steps}")
    await self._send_command(f"WR DM25 {num_positions}")
    await self._send_command(f"WR DM5 {position}")
    return cassette, position

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name!r} not found in StoreX {self.name!r}")

  def _find_available_sites_sorted(self, plate: Plate) -> List[PlateHolder]:
    plate_height = plate.get_size_z() + 3 if plate.has_lid() else plate.get_size_z()
    sites = [
      site
      for rack in self._racks
      for site in rack.get_free_sites()
      if site.get_size_z() >= plate_height
    ]
    if not sites:
      raise NoFreeSiteError(f"No free site found in StoreX {self.name!r} for plate {plate.name!r}")
    return sorted(sites, key=lambda site: site.get_size_z())

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    return self._find_available_sites_sorted(plate)[0]

  def find_random_site_for_plate(self, plate: Plate) -> PlateHolder:
    return random.choice(self._find_available_sites_sorted(plate))

  async def initialize(self) -> None:
    """Home and activate the plate handler."""
    await self._send_command("ST 1900")
    await self._send_command("ST 1801")
    await self._wait_ready()

  async def open_door(self) -> None:
    await self._send_command("ST 1901")
    await self._wait_ready()

  async def close_door(self) -> None:
    await self._send_command("ST 1902")
    await self._wait_ready()

  async def fetch_plate_to_loading_tray(self, plate_name: str, read_barcode: bool = False) -> Plate:
    """Move a named stored plate to the loading tray."""
    if self.loading_tray.resource is not None:
      raise ValueError(f"StoreX loading tray {self.loading_tray.name!r} is occupied")
    site = self.get_site_by_plate_name(plate_name)
    plate = cast(Plate, site.resource)
    cassette, position = await self._configure_site(site)
    if read_barcode:
      barcode = await self._read_barcode_inline(cassette, position)
      if barcode is not None:
        plate.barcode = barcode
    await self._send_command("ST 1905")
    await self._wait_ready()
    await self._send_command("ST 1903")
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    return plate

  async def take_in_plate(
    self, site: StorageSite = "smallest", read_barcode: bool = False
  ) -> Plate:
    """Move the plate on the loading tray into a rack site."""
    plate = self.loading_tray.resource
    if not isinstance(plate, Plate):
      raise ResourceNotFoundError(f"No plate on StoreX loading tray {self.loading_tray.name!r}")
    if site == "smallest":
      destination = self.find_smallest_site_for_plate(plate)
    elif site == "random":
      destination = self.find_random_site_for_plate(plate)
    elif isinstance(site, PlateHolder):
      if site not in self._find_available_sites_sorted(plate):
        raise ValueError(f"Site {site.name!r} is not available for plate {plate.name!r}")
      destination = site
    else:
      raise ValueError(f"Invalid StoreX storage site: {site!r}")

    cassette, position = await self._configure_site(destination)
    await self._send_command("ST 1904")
    await self._wait_ready()
    if read_barcode:
      barcode = await self._read_barcode_inline(cassette, position)
      if barcode is not None:
        plate.barcode = barcode
    await self._send_command("ST 1903")
    plate.unassign()
    destination.assign_child_resource(plate)
    return plate

  async def move_plate(
    self, plate_name: str, destination: PlateHolder, read_barcode: bool = False
  ) -> Plate:
    """Move a named plate between two internal rack sites."""
    if destination.resource is not None:
      raise ValueError(f"Destination site {destination.name!r} is occupied")
    origin = self.get_site_by_plate_name(plate_name)
    plate = cast(Plate, origin.resource)
    self._site_to_address(destination)
    self._carrier_to_steps_and_positions(destination)

    origin_cassette, origin_position = await self._configure_site(origin)
    if read_barcode:
      barcode = await self._read_barcode_inline(origin_cassette, origin_position)
      if barcode is not None:
        plate.barcode = barcode
    await self._send_command("ST 1908")
    await self._wait_ready()

    await self._configure_site(destination)
    await self._send_command("ST 1909")
    await self._wait_ready()
    await self._send_command("ST 1903")
    plate.unassign()
    destination.assign_child_resource(plate)
    return plate

  def _require_barcode_scanner(self) -> BarcodeScanner:
    if self.barcode_scanner is None:
      raise RuntimeError("No barcode scanner is configured for this StoreX")
    return self.barcode_scanner

  async def _read_barcode_inline(self, cassette: int, plate_position: int) -> Optional[Barcode]:
    scanner = self._require_barcode_scanner()
    await self._send_command("ST 1910")
    await self._wait_ready()
    try:
      barcode = await scanner.scan_barcode()
    finally:
      response = await self._send_command("RS 1910")
      if response != "OK":
        raise RuntimeError("Failed to reset shovel position after barcode reading")
      await self._wait_ready()
    logger.info(
      "[StoreX %s] barcode: cassette=%d position=%d value=%s",
      self.io.port,
      cassette,
      plate_position,
      barcode,
    )
    return barcode

  async def scan_barcode(self, site: PlateHolder) -> Optional[Barcode]:
    """Scan the plate in an internal rack site."""
    cassette, position = await self._configure_site(site)
    barcode = await self._read_barcode_inline(cassette, position)
    await self._send_command("ST 1903")
    if barcode is not None and isinstance(site.resource, Plate):
      site.resource.barcode = barcode
    return barcode

  async def set_temperature(self, temperature: float) -> None:
    if not self.supports_temperature_control:
      raise NotImplementedError("Temperature control is not supported on this model")
    await self._send_command(f"WR DM890 {int(temperature * 10):05d}")
    await self._wait_ready()

  async def request_current_temperature(self) -> float:
    if not self.supports_temperature_control:
      raise NotImplementedError("Temperature control is not supported on this model")
    response = await self._send_command("RD DM982")
    try:
      return int(response) / 10.0
    except ValueError:
      raise RuntimeError(f"Invalid temperature value received from StoreX: {response!r}")

  async def request_target_temperature(self) -> float:
    if not self.supports_temperature_control:
      raise NotImplementedError("Temperature control is not supported on this model")
    response = await self._send_command("RD DM890")
    try:
      return int(response) / 10.0
    except ValueError:
      raise RuntimeError(f"Invalid target temperature received from StoreX: {response!r}")

  async def set_humidity(self, humidity: float) -> None:
    if not self.supports_humidity_control:
      raise NotImplementedError("Independent humidity control is not supported on this model")
    self._validate_fraction("humidity", humidity)
    await self._send_command(f"WR DM893 {int(humidity * 1000):05d}")
    await self._wait_ready()

  async def request_current_humidity(self) -> float:
    if not self.supports_humidity_control:
      raise NotImplementedError("Independent humidity control is not supported on this model")
    return await self._request_fraction("RD DM983", 1000, "humidity")

  async def request_target_humidity(self) -> float:
    if not self.supports_humidity_control:
      raise NotImplementedError("Independent humidity control is not supported on this model")
    return await self._request_fraction("RD DM893", 1000, "target humidity")

  async def set_co2_level(self, co2_level: float) -> None:
    self._validate_fraction("CO2 level", co2_level)
    await self._send_command(f"WR DM894 {int(co2_level * 10000):05d}")
    await self._wait_ready()

  async def request_co2_level(self) -> float:
    return await self._request_fraction("RD DM984", 10000, "CO2 level")

  async def request_target_co2_level(self) -> float:
    return await self._request_fraction("RD DM894", 10000, "target CO2 level")

  async def set_n2_level(self, n2_level: float) -> None:
    self._validate_fraction("N2 level", n2_level)
    await self._send_command(f"WR DM895 {int(n2_level * 10000):05d}")
    await self._wait_ready()

  async def request_n2_level(self) -> float:
    return await self._request_fraction("RD DM985", 10000, "N2 level")

  async def request_target_n2_level(self) -> float:
    return await self._request_fraction("RD DM895", 10000, "target N2 level")

  @staticmethod
  def _validate_fraction(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
      raise ValueError(f"{name} must be between 0.0 and 1.0")

  async def _request_fraction(self, command: str, scale: int, name: str) -> float:
    response = await self._send_command(command)
    try:
      return int(response) / scale
    except ValueError:
      raise RuntimeError(f"Invalid {name} received from StoreX: {response!r}")

  def _require_shaker(self) -> None:
    if not self.has_shaker:
      raise NotImplementedError("This StoreX was not configured with a shaker")

  async def request_shaker_speed(self) -> float:
    self._require_shaker()
    response = await self._send_command("RD DM39")
    try:
      return int(response) / 10.0
    except ValueError:
      raise RuntimeError(f"Invalid shaker speed received from StoreX: {response!r}")

  async def start_shaking(self, frequency: float) -> None:
    self._require_shaker()
    if not 1.0 <= frequency <= 50.0:
      raise ValueError("Shaking frequency must be between 1.0 and 50.0 Hz")
    await self._send_command(f"WR DM39 {int(frequency * 10):05d}")
    await self._send_command("ST 1913")
    await self._wait_ready()

  async def stop_shaking(self) -> None:
    self._require_shaker()
    await self._send_command("RS 1913")
    await self._wait_ready()

  async def shake(self, frequency: float, duration: float) -> None:
    if duration < 0:
      raise ValueError("Shaking duration cannot be negative")
    await self.start_shaking(frequency)
    try:
      await asyncio.sleep(duration)
    finally:
      await self.stop_shaking()

  async def request_swap_station_is_home(self) -> bool:
    response = await self._send_command("RD 1912")
    if response not in {"0", "1"}:
      raise RuntimeError(f"Unexpected swap station response: {response!r}")
    return response == "0"

  async def move_swap_station_home(self) -> None:
    if not await self.request_swap_station_is_home():
      await self._send_command("RS 1912")

  async def move_swap_station_swapped(self) -> None:
    if await self.request_swap_station_is_home():
      await self._send_command("ST 1912")

  async def request_shovel_sensor(self) -> bool:
    await self._send_command("ST 1911")
    await asyncio.sleep(0.1)
    return await self._request_binary_sensor("RD 1812", "shovel")

  async def request_transfer_sensor(self) -> bool:
    return await self._request_binary_sensor("RD 1813", "transfer station")

  async def request_second_transfer_sensor(self) -> bool:
    return await self._request_binary_sensor("RD 1807", "second transfer station")

  async def _request_binary_sensor(self, command: str, name: str) -> bool:
    response = await self._send_command(command)
    if response == "1":
      return True
    if response == "0":
      return False
    raise RuntimeError(f"Unexpected {name} sensor response: {response!r}")

  def summary(self) -> str:
    """Return a compact table of the plates in each cassette."""
    headers = [f"Rack {index + 1}" for index in range(len(self._racks))]
    columns = [
      [site.resource.name if site.resource else "<empty>" for site in reversed(rack.sites.values())]
      for rack in self._racks
    ]
    widths = [
      max(len(str(value)) for value in [headers[index], *columns[index]])
      for index in range(len(headers))
    ]

    def separator() -> str:
      return "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def row(values: Sequence[str]) -> str:
      return (
        "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(values)) + " |"
      )

    lines = [separator(), row(headers), separator()]
    lines.extend(row(values) for values in zip(*columns))
    lines.append(separator())
    return "\n".join(lines)
