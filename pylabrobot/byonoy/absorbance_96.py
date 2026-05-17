import asyncio
import logging
import time
from typing import List, Optional, Tuple

from pylabrobot.byonoy.driver import ABS96_ERROR_NAMES, ByonoyDevice, ByonoyDriver
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.absorbance import (
  Absorbance,
  AbsorbanceBackend,
  AbsorbanceResult,
)
from pylabrobot.device import Device
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.list import reshape_2d

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class ByonoyAbsorbance96Backend(ByonoyDriver, AbsorbanceBackend):
  """Backend for the Byonoy Absorbance 96 Automate plate reader."""

  _ERROR_NAMES = ABS96_ERROR_NAMES

  def __init__(self) -> None:
    super().__init__(pid=0x1199, device_type=ByonoyDevice.ABSORBANCE_96, name="Byonoy A96")
    self.available_wavelengths: List[float] = []

  async def setup(self, backend_params: Optional["BackendParams"] = None) -> None:
    await super().setup(backend_params=backend_params)
    await self.initialize_measurements()
    self.available_wavelengths = await self.request_available_absorbance_wavelengths()
    logger.info(
      "[%s] ready, available wavelengths: %s nm",
      self.name,
      self.available_wavelengths,
    )

  async def request_available_absorbance_wavelengths(self) -> List[float]:
    response = await self.send_command(
      report_id=0x0330,
      payload=b"\x00" * 60,
      wait_for_response=True,
      routing_info=b"\x80\x40",
    )
    assert response is not None, "Failed to get available wavelengths."
    reader = Reader(response[2:])
    available_wavelengths = [reader.i16() for _ in range(30)]
    return [w for w in available_wavelengths if w != 0]

  async def _run_abs_measurement(self, signal_wl: int, reference_wl: int, is_reference: bool):
    with self._measurement_in_flight(0x0320):
      await self.send_command(
        report_id=0x0010,
        payload=b"\x00" * 60,
        wait_for_response=False,
      )

      payload2 = Writer().u16(7).u8(0).raw_bytes(b"\x00" * 52).finish()
      await self.send_command(
        report_id=0x0200,
        payload=payload2,
        wait_for_response=False,
      )

      payload3 = Writer().i16(signal_wl).i16(reference_wl).u8(int(is_reference)).u8(0).finish()
      await self.send_command(
        report_id=0x0320,
        payload=payload3,
        wait_for_response=False,
        routing_info=b"\x00\x40",
      )

      # Index by seq so out-of-order/dropped chunks surface as None slots
      # rather than silently shifting subsequent rows into the wrong wells.
      rows_by_seq: List[Optional[List[float]]] = []
      flags_by_seq: List[Optional[int]] = []
      expected_chunks: Optional[int] = None
      t0 = time.time()

      while True:
        if self._abort_requested:
          logger.info("[%s] measurement aborted by cancel()", self.name)
          raise asyncio.CancelledError("Absorbance measurement aborted via cancel().")
        if time.time() - t0 > 120:
          logger.error(
            "[%s] measurement timed out after 120s (signal=%d nm, ref=%d nm)",
            self.name,
            signal_wl,
            reference_wl,
          )
          raise TimeoutError("Measurement timeout.")

        chunk = await self.io.read(64, timeout=30)
        if len(chunk) == 0:
          continue

        reader = Reader(chunk)
        report_id = reader.u16()

        if report_id == 0x0500:
          seq = reader.u8()
          seq_len = reader.u8()
          _ = reader.i16()  # signal_wl_nm
          _ = reader.i16()  # reference_wl_nm
          _ = reader.u32()  # duration_ms
          row = [reader.f32() for _ in range(12)]
          flags = reader.u8()
          _ = reader.u8()  # progress (0..100 running %); not surfaced

          if seq_len == 0:
            raise RuntimeError(f"{self.name} firmware sent chunk with seq_len=0")
          if expected_chunks is None:
            expected_chunks = seq_len
            rows_by_seq = [None] * seq_len
            flags_by_seq = [None] * seq_len
          elif seq_len != expected_chunks:
            raise RuntimeError(
              f"{self.name} firmware changed seq_len mid-stream: {expected_chunks} → {seq_len}"
            )
          if not 0 <= seq < seq_len:
            raise RuntimeError(f"{self.name} firmware sent seq={seq} (seq_len={seq_len})")
          rows_by_seq[seq] = row
          flags_by_seq[seq] = flags

          if all(r is not None for r in rows_by_seq):
            break

    if expected_chunks is None:
      raise RuntimeError(f"{self.name} absorbance read produced no chunks")
    chunk_flags: List[int] = [f for f in flags_by_seq if f is not None]
    rows: List[float] = [v for r in rows_by_seq if r is not None for v in r]

    status = await self.request_status()
    if status.error_code != 0:
      raise RuntimeError(
        f"{self.name} firmware error after measurement (signal={signal_wl} nm, "
        f"ref={reference_wl} nm): {self.describe_error_code(status.error_code)} "
        f"(chunk flags: {[f'0x{f:02x}' for f in chunk_flags]})"
      )
    self._warn_chunk_flags(chunk_flags)

    return rows

  async def initialize_measurements(self):
    REFERENCE_WL = 0
    SIGNAL_WL = 660
    await self._run_abs_measurement(
      signal_wl=SIGNAL_WL,
      reference_wl=REFERENCE_WL,
      is_reference=True,
    )

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    if wavelength not in self.available_wavelengths:
      raise ValueError(
        f"Wavelength {wavelength} nm not in available wavelengths {self.available_wavelengths}."
      )

    logger.info(
      "[%s] reading absorbance: plate='%s', wavelength=%d nm, wells=%d/%d",
      self.name,
      plate.name,
      wavelength,
      len(wells),
      plate.num_items,
    )
    rows = await self._run_abs_measurement(
      signal_wl=wavelength,
      reference_wl=0,
      is_reference=False,
    )

    matrix = reshape_2d(rows, (8, 12))

    return [
      AbsorbanceResult(
        data=matrix,
        wavelength=wavelength,
        temperature=None,
        timestamp=time.time(),
      )
    ]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class _ByonoyAbsorbanceReaderPlateHolder(PlateHolder):
  """Plate holder with interlock: blocks drops while illumination unit is on the base."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    pedestal_size_z: float = 0,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "plate_holder",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      pedestal_size_z=pedestal_size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    self._byonoy_base: Optional[ByonoyAbsorbanceBaseUnit] = None

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    if self._byonoy_base is None:
      raise RuntimeError(
        "Plate holder not assigned to a ByonoyAbsorbanceBaseUnit. This should not happen."
      )
    if self._byonoy_base.illumination_unit_holder.resource is not None:
      raise RuntimeError(
        f"Cannot drop resource {resource.name} onto plate holder while illumination unit is on "
        "the base. Please remove the illumination unit from the base before dropping a resource."
      )
    super().check_can_drop_resource_here(resource, reassign=reassign)


class ByonoyAbsorbanceBaseUnit(Resource):
  def __init__(
    self,
    name: str,
    size_x: float = 155.26,
    size_y: float = 95.48,
    size_z: float = 18.5,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    barcode: Optional[Barcode] = None,
    preferred_pickup_location: Optional[Coordinate] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      barcode=barcode,
      preferred_pickup_location=preferred_pickup_location,
    )

    self.plate_holder = _ByonoyAbsorbanceReaderPlateHolder(
      name=self.name + "_plate_holder",
      size_x=127.76,
      size_y=85.59,
      size_z=0,
      child_location=Coordinate(x=22.5, y=5.0, z=16.0),
      pedestal_size_z=0,
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

    self.illumination_unit_holder = ResourceHolder(
      name=self.name + "_illumination_unit_holder",
      size_x=size_x,
      size_y=size_y,
      size_z=0,
      child_location=Coordinate(x=0, y=0, z=14.1),
    )
    self.assign_child_resource(self.illumination_unit_holder, location=Coordinate.zero())

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign: bool = True
  ) -> None:
    if isinstance(resource, _ByonoyAbsorbanceReaderPlateHolder):
      if self.plate_holder._byonoy_base is not None:
        raise ValueError("ByonoyDriver can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    raise RuntimeError(
      "ByonoyDriver does not support assigning child resources directly. "
      "Use the plate_holder or illumination_unit_holder to assign plates and the "
      "illumination unit, respectively."
    )


def byonoy_sbs_adapter(name: str) -> ResourceHolder:
  """Create a Byonoy SBS adapter ResourceHolder."""
  return ResourceHolder(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=17.0,
    child_location=Coordinate(
      x=-(155.26 - 127.76) / 2,
      y=-(95.48 - 85.48) / 2,
      z=17.0,
    ),
  )


def byonoy_a96a_illumination_unit(name: str) -> Resource:
  size_x = 155.26
  size_y = 95.48
  return Resource(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=42.898,
    model="Byonoy A96A Illumination Unit",
    preferred_pickup_location=Coordinate(x=size_x / 2, y=size_y / 2, z=29.5),
  )


# ---------------------------------------------------------------------------
# Device + Resource composite
# ---------------------------------------------------------------------------


class ByonoyAbsorbance96(ByonoyAbsorbanceBaseUnit, Device):
  """Byonoy Absorbance 96 Automate plate reader."""

  def __init__(self, name: str = "byonoy_absorbance_96"):
    backend = ByonoyAbsorbance96Backend()
    ByonoyAbsorbanceBaseUnit.__init__(self, name=name + "_base")
    Device.__init__(self, driver=backend)
    self.driver: ByonoyAbsorbance96Backend = backend
    self.absorbance = Absorbance(backend=backend)
    self._capabilities = [self.absorbance]

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}


def byonoy_a96a_detection_unit(name: str) -> ByonoyAbsorbance96:
  """Create a Byonoy A96A detection unit."""
  return ByonoyAbsorbance96(name=name)


def byonoy_a96a_parking_unit(name: str) -> ByonoyAbsorbanceBaseUnit:
  """Create a Byonoy A96A detection unit holder (base only, no backend)."""
  return ByonoyAbsorbanceBaseUnit(name=name)


def byonoy_a96a(name: str, assign: bool = True) -> Tuple[ByonoyAbsorbance96, Resource]:
  """Create a full Byonoy A96A setup (reader + illumination unit)."""
  reader = byonoy_a96a_detection_unit(name=name + "_reader")
  illumination_unit = byonoy_a96a_illumination_unit(name=name + "_illumination_unit")
  if assign:
    reader.illumination_unit_holder.assign_child_resource(illumination_unit)
  return reader, illumination_unit
