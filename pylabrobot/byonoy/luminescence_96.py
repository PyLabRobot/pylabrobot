import time
from typing import List, Optional, Tuple

from pylabrobot.byonoy.backend import ByonoyBase, ByonoyDevice
from pylabrobot.capabilities.plate_reading.luminescence import (
  LuminescenceBackend,
  LuminescenceCapability,
  LuminescenceResult,
)
from pylabrobot.device import Device
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.well import Well
from pylabrobot.utils.list import reshape_2d


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class ByonoyLuminescence96Backend(ByonoyBase, LuminescenceBackend):
  """Backend for the Byonoy Luminescence 96 Automate plate reader."""

  def __init__(self) -> None:
    super().__init__(pid=0x119B, device_type=ByonoyDevice.LUMINESCENCE_96)

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float, integration_time: float = 2
  ) -> List[LuminescenceResult]:
    """Read luminescence.

    Args:
      plate: The plate being read.
      wells: Wells to measure.
      focal_height: Focal height in mm.
      integration_time: Integration time in seconds, default 2 s.
    """

    await self.send_command(
      report_id=0x0010,
      payload=b"\x00" * 60,
      wait_for_response=False,
    )

    payload2 = (
      Writer()
      .u16(7)
      .u8(0)
      .raw_bytes(b"\x00" * 52)
      .finish()
    )
    await self.send_command(
      report_id=0x0200,
      payload=payload2,
      wait_for_response=False,
    )

    payload3 = (
      Writer()
      .i32(int(integration_time * 1000 * 1000))
      .raw_bytes(b"\xff" * 12)
      .u8(0)
      .u8(0)
      .finish()
    )
    await self.send_command(
      report_id=0x0340,
      payload=payload3,
      wait_for_response=False,
    )

    t0 = time.time()
    all_rows: List[float] = []

    while True:
      if time.time() - t0 > 120:
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      reader = Reader(chunk)
      report_id = reader.u16()

      if report_id == 0x0600:
        seq = reader.u8()
        seq_len = reader.u8()
        _ = reader.u32()  # integration_time_us
        _ = reader.u32()  # duration_ms
        row = [reader.f32() for _ in range(12)]
        _ = reader.u8()  # flags
        _ = reader.u8()  # progress

        all_rows.extend(row)

        if seq == seq_len - 1:
          break

    hybrid_result = all_rows[96 * 0 : 96 * 1]

    return [
      LuminescenceResult(
        data=reshape_2d(hybrid_result, (8, 12)),
        temperature=None,
        timestamp=time.time(),
      )
    ]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class _ByonoyLuminescenceReaderPlateHolder(PlateHolder):
  """Plate holder with interlock: blocks drops while reader unit is on the base."""

  def __init__(
    self,
    name: str,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "plate_holder",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=127.76,
      size_y=85.59,
      size_z=0,
      pedestal_size_z=0,
      child_location=child_location,
      category=category,
      model=model,
    )
    self._byonoy_base: Optional[ByonoyLuminescenceBaseUnit] = None

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    if self._byonoy_base is None:
      raise RuntimeError(
        "Plate holder not assigned to a ByonoyLuminescenceBaseUnit. This should not happen."
      )
    if self._byonoy_base.reader_unit_holder.resource is not None:
      raise RuntimeError(
        f"Cannot drop resource {resource.name} onto plate holder while reader unit is on "
        "the base. Please remove the reader unit from the base before dropping a resource."
      )
    super().check_can_drop_resource_here(resource, reassign=reassign)


class ByonoyLuminescenceBaseUnit(Resource):
  """Base unit for the Byonoy L96/L96A luminescence reader."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    plate_holder_child_location: Coordinate,
    reader_unit_holder_child_location: Coordinate,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    barcode: Optional[Barcode] = None,
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
    )

    self.plate_holder = _ByonoyLuminescenceReaderPlateHolder(
      name=self.name + "_plate_holder",
      child_location=plate_holder_child_location,
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

    self.reader_unit_holder = ResourceHolder(
      name=self.name + "_reader_unit_holder",
      size_x=size_x,
      size_y=size_y,
      size_z=0,
      child_location=reader_unit_holder_child_location,
    )
    self.assign_child_resource(self.reader_unit_holder, location=Coordinate.zero())

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate], reassign: bool = True
  ) -> None:
    if isinstance(resource, _ByonoyLuminescenceReaderPlateHolder):
      if self.plate_holder._byonoy_base is not None:
        raise ValueError("ByonoyBase can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    raise RuntimeError(
      "ByonoyBase does not support assigning child resources directly. "
      "Use the plate_holder or reader_unit_holder to assign plates and the reader unit, "
      "respectively."
    )


# ---------------------------------------------------------------------------
# Device (reader unit — sits on top of a ByonoyLuminescenceBaseUnit)
# ---------------------------------------------------------------------------


class ByonoyLuminescence96(Resource, Device):
  """Byonoy Luminescence 96 reader unit."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    preferred_pickup_location: Optional[Coordinate] = None,
  ):
    backend = ByonoyLuminescence96Backend()
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Byonoy L96 Reader Unit",
      preferred_pickup_location=preferred_pickup_location,
    )
    Device.__init__(self, backend=backend)
    self._backend: ByonoyLuminescence96Backend = backend
    self.luminescence = LuminescenceCapability(backend=backend)
    self._capabilities = [self.luminescence]

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def byonoy_l96_reader_unit(name: str) -> ByonoyLuminescence96:
  """Create a Byonoy L96 reader unit (non-automate, no preferred pickup)."""
  return ByonoyLuminescence96(
    name=name,
    size_x=139.7,
    size_y=97.5,
    size_z=35,
    preferred_pickup_location=None,
  )


def byonoy_l96_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Create a Byonoy L96 base unit."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=139.7,
    size_y=97.5,
    size_z=9.4,
    plate_holder_child_location=Coordinate(x=6.25, y=6.1, z=2.64),
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=7.2),
  )


def byonoy_l96(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96]:
  """Create a full Byonoy L96 setup (base + reader)."""
  base_unit = byonoy_l96_base_unit(name=name + "_base")
  reader_unit = byonoy_l96_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit


def byonoy_l96a_reader_unit(name: str) -> ByonoyLuminescence96:
  """Create a Byonoy L96A reader unit (automate, with preferred pickup)."""
  return ByonoyLuminescence96(
    name=name,
    size_x=138,
    size_y=97.5,
    size_z=41.7,
    preferred_pickup_location=Coordinate(x=69, y=48.75, z=33.2),
  )


def byonoy_l96a_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Create a Byonoy L96A base unit."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=138,
    size_y=97.5,
    size_z=10.7,
    plate_holder_child_location=Coordinate(x=5.1, y=4.75, z=8),
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=6.3),
  )


def byonoy_l96a(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96]:
  """Create a full Byonoy L96A setup (base + reader)."""
  base_unit = byonoy_l96a_base_unit(name=name + "_base")
  reader_unit = byonoy_l96a_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit
