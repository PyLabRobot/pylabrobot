"""Legacy. Use pylabrobot.liconic.LiconicBackend instead."""

from typing import List, Union

from pylabrobot.capabilities.barcode_scanning.backend import (
  BarcodeScannerBackend as _NewBarcodeScannerBackend,
)
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.liconic import backend as new_liconic
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.carrier import PlateCarrier

# Re-export for legacy imports
LICONIC_SITE_HEIGHT_TO_STEPS = new_liconic.LICONIC_SITE_HEIGHT_TO_STEPS


class _BarcodeScannerAdapter(_NewBarcodeScannerBackend):
  """Adapts a legacy BarcodeScanner Machine to the new BarcodeScannerBackend interface."""

  def __init__(self, legacy_scanner):
    self._legacy = legacy_scanner

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def scan_barcode(self) -> Barcode:
    result: Barcode = await self._legacy.scan()
    return result


class ExperimentalLiconicBackend(IncubatorBackend):
  """Legacy. Use pylabrobot.liconic.LiconicBackend instead."""

  # Internal attributes that should be forwarded to self._new for test compatibility
  _FORWARDED_ATTRS = {
    "_send_command",
    "_wait_ready",
    "_wait_plate_ready",
    "_carrier_to_steps_pos",
    "_site_to_m_n",
    "_racks",
    "io",
  }

  def __init__(
    self,
    model: Union["new_liconic.LiconicType", str],
    port: str,
    barcode_scanner=None,
  ):
    super().__init__()
    self._new = new_liconic.LiconicBackend(model=model, port=port)
    self.barcode_scanner = barcode_scanner

  @property
  def _barcode_adapter(self) -> _BarcodeScannerAdapter:
    """Wrap the legacy BarcodeScanner Machine for the new backend interface."""
    assert self.barcode_scanner is not None, "Barcode scanner not configured"
    return _BarcodeScannerAdapter(self.barcode_scanner)

  def __getattr__(self, name):
    if name in self._FORWARDED_ATTRS:
      return getattr(self._new, name)
    raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

  def __setattr__(self, name, value):
    if name != "_new" and hasattr(self, "_new") and name in self._FORWARDED_ATTRS:
      setattr(self._new, name, value)
    else:
      super().__setattr__(name, value)

  @property
  def model(self):
    return self._new.model

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    await self._new.set_racks(racks)

  async def initialize(self):
    await self._new.initialize()

  async def open_door(self):
    await self._new.open_door()

  async def close_door(self):
    await self._new.close_door()

  async def fetch_plate_to_loading_tray(
    self, plate: Plate, read_barcode: bool = False, **backend_kwargs
  ):
    if not read_barcode:
      await self._new.fetch_plate_to_loading_tray(plate)
      return

    # Can't use the bundled fetch method because the original interleaves barcode reading
    # between DM writes and ST 1905. Replicate the original command sequence.
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")
    site = plate.parent
    assert isinstance(site, PlateHolder)
    m, n = self._new._site_to_m_n(site)
    step_size, pos_num = self._new._carrier_to_steps_pos(site)

    await self._new._send_command(f"WR DM0 {m}")
    await self._new._send_command(f"WR DM23 {step_size}")
    await self._new._send_command(f"WR DM25 {pos_num}")
    await self._new._send_command(f"WR DM5 {n}")

    plate.barcode = await self._new.read_barcode_inline(m, n, self._barcode_adapter)

    await self._new._send_command("ST 1905")
    await self._new._wait_ready()
    await self._new._send_command("ST 1903")

  async def take_in_plate(
    self, plate: Plate, site: PlateHolder, read_barcode: bool = False, **backend_kwargs
  ):
    if not read_barcode:
      await self._new.store_plate(plate, site)
      return

    # Can't use the bundled store method because the original reads barcode
    # AFTER ST 1904/wait but BEFORE ST 1903 (terminate access).
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")
    m, n = self._new._site_to_m_n(site)
    step_size, pos_num = self._new._carrier_to_steps_pos(site)

    await self._new._send_command(f"WR DM0 {m}")
    await self._new._send_command(f"WR DM23 {step_size}")
    await self._new._send_command(f"WR DM25 {pos_num}")
    await self._new._send_command(f"WR DM5 {n}")
    await self._new._send_command("ST 1904")
    await self._new._wait_ready()

    plate.barcode = await self._new.read_barcode_inline(m, n, self._barcode_adapter)

    await self._new._send_command("ST 1903")

  async def move_position_to_position(
    self, plate: Plate, dest_site: PlateHolder, read_barcode: bool = False
  ):
    if not read_barcode:
      await self._new.move_position_to_position(plate, dest_site)
      return

    # Can't use the bundled move method because the original reads barcode
    # AFTER DM writes but BEFORE ST 1908 (pick plate).
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")
    orig_site = plate.parent
    assert isinstance(orig_site, PlateHolder)
    assert isinstance(dest_site, PlateHolder)

    if dest_site.resource is not None:
      raise RuntimeError(f"Position {dest_site} already has a plate assigned!")

    orig_m, orig_n = self._new._site_to_m_n(orig_site)
    dest_m, dest_n = self._new._site_to_m_n(dest_site)
    orig_step_size, orig_pos_num = self._new._carrier_to_steps_pos(orig_site)
    dest_step_size, dest_pos_num = self._new._carrier_to_steps_pos(dest_site)

    await self._new._send_command(f"WR DM0 {orig_m}")
    await self._new._send_command(f"WR DM23 {orig_step_size}")
    await self._new._send_command(f"WR DM25 {orig_pos_num}")
    await self._new._send_command(f"WR DM5 {orig_n}")

    plate.barcode = await self._new.read_barcode_inline(orig_m, orig_n, self._barcode_adapter)

    await self._new._send_command("ST 1908")
    await self._new._wait_ready()

    if orig_m != dest_m:
      await self._new._send_command(f"WR DM0 {dest_m}")
    await self._new._send_command(f"WR DM23 {dest_step_size}")
    await self._new._send_command(f"WR DM25 {dest_pos_num}")
    await self._new._send_command(f"WR DM5 {dest_n}")
    await self._new._send_command("ST 1909")
    await self._new._wait_ready()
    await self._new._send_command("ST 1903")

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature)

  async def get_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def start_shaking(self, frequency):
    await self._new.start_shaking(speed=frequency)

  async def stop_shaking(self):
    await self._new.stop_shaking()

  async def get_shaker_speed(self) -> float:
    return await self._new.get_shaker_speed()

  async def shaker_status(self) -> int:
    raise NotImplementedError("shaker_status command not yet implemented")

  async def get_target_temperature(self) -> float:
    return await self._new.get_target_temperature()

  async def set_humidity(self, humidity: float):
    await self._new.set_humidity(humidity)

  async def get_humidity(self) -> float:
    return await self._new.get_current_humidity()

  async def get_target_humidity(self) -> float:
    return await self._new.get_target_humidity()

  async def set_co2_level(self, co2_level: float):
    await self._new.set_co2_level(co2_level)

  async def get_co2_level(self) -> float:
    return await self._new.get_co2_level()

  async def get_target_co2_level(self) -> float:
    return await self._new.get_target_co2_level()

  async def set_n2_level(self, n2_level: float):
    await self._new.set_n2_level(n2_level)

  async def get_n2_level(self) -> float:
    return await self._new.get_n2_level()

  async def get_target_n2_level(self) -> float:
    return await self._new.get_target_n2_level()

  async def turn_swap_station(self, home: bool):
    await self._new.turn_swap_station(home)

  async def check_shovel_sensor(self) -> bool:
    return await self._new.check_shovel_sensor()

  async def check_transfer_sensor(self) -> bool:
    return await self._new.check_transfer_sensor()

  async def check_second_transfer_sensor(self) -> bool:
    return await self._new.check_second_transfer_sensor()

  async def read_barcode_inline(self, cassette: int, plt_position: int):
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")
    return await self._new.read_barcode_inline(cassette, plt_position, self._barcode_adapter)

  async def scan_barcode(self, site: PlateHolder):
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")
    return await self._new.scan_barcode(site, self._barcode_adapter)

  def serialize(self) -> dict:
    return self._new.serialize()

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"], model=data["model"])
