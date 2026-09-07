import asyncio
import logging
import time
from datetime import datetime
from typing import List, Optional, Tuple

from pylabrobot.byonoy.driver import (
  LUM96_PRESET_S,
  ByonoyDevice,
  ByonoyDriver,
  Lum96IntegrationMode,
  encode_well_bitmask,
)
from pylabrobot.byonoy.results import LuminescenceResult
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.resources import Coordinate, PlateHolder, Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.well import Well
from pylabrobot.utils.list import reshape_2d

logger = logging.getLogger(__name__)


class ByonoyLuminescence96(Resource, ByonoyDriver):
  """The Byonoy Luminescence 96 Automate reader unit.

  ``Resource`` comes first in the MRO so its ``name`` property (and its setter,
  which the resource tree needs) wins over the driver's device-name property.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = "Byonoy L96 Reader Unit",
    barcode: Optional[Barcode] = None,
    preferred_pickup_location: Optional[Coordinate] = None,
  ) -> None:
    Resource.__init__(
      self,
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
    ByonoyDriver.__init__(
      self, pid=0x119B, device_type=ByonoyDevice.LUMINESCENCE_96, name="Byonoy L96"
    )

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    mode: Lum96IntegrationMode = "sensitive",
    integration_time: Optional[float] = None,
  ) -> List[LuminescenceResult]:
    """Read luminescence.

    Args:
      plate: The plate being read.
      wells: Wells to measure.
      focal_height: Required by the abstract :class:`LuminescenceBackend`
        contract but **ignored on the Byonoy L96** — the device has a
        fixed optical configuration (the detector unit clamps onto the
        base; the optical path is determined by plate + base + detector
        geometry, not user-tunable). Passing any value is harmless;
        passing 0 is conventional.
      mode: One of "rapid" (100 ms), "sensitive" (2 s, default),
        "ultra_sensitive" (20 s), or "custom". Presets match the
        byonoy_device_library mapping.
      integration_time: Integration time in seconds. If set, forces "custom"
        mode regardless of `mode`. Required when `mode == "custom"`.
    """
    # Resolve mode + integration time
    if integration_time is not None:
      mode = "custom"
    elif mode == "custom":
      raise ValueError("'custom' mode requires integration_time to be set.")
    else:
      integration_time = LUM96_PRESET_S[mode]

    # Firmware always scans all 96 wells; this mask only filters which are
    # reported (others come back as 0.0). Single source of truth: the wells arg.
    well_set = set(id(w) for w in wells)
    mask_bools = [id(w) in well_set for w in plate.get_all_items()]

    well_mask = encode_well_bitmask(mask_bools, n=96)
    logger.info(
      "[%s] reading luminescence: plate='%s', mode=%s, integration_time=%.3fs, wells=%d/96",
      self.name,
      plate.name,
      mode,
      integration_time,
      sum(mask_bools),
    )

    with self._measurement_in_flight(0x0340):
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

      payload3 = (
        Writer()
        .i32(int(integration_time * 1_000_000))
        .raw_bytes(well_mask)
        .u8(0)  # is_reference_measurement
        .u8(0)  # flags
        .finish()
      )
      await self.send_command(
        report_id=0x0340,
        payload=payload3,
        wait_for_response=False,
      )

      t0 = time.time()
      # Index by seq so an out-of-order or dropped chunk surfaces as a None
      # slot instead of silently shifting subsequent rows into the wrong wells.
      rows_by_seq: List[Optional[List[float]]] = []
      flags_by_seq: List[Optional[int]] = []
      expected_chunks: Optional[int] = None

      while True:
        if self._abort_requested:
          logger.info("[%s] read aborted by cancel()", self.name)
          raise asyncio.CancelledError("Luminescence read aborted via cancel().")
        if time.time() - t0 > 120:
          logger.error("[%s] luminescence read timed out after 120s", self.name)
          raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

        chunk = await self.io.read(64, timeout=2)
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
      raise RuntimeError(f"{self.name} luminescence read produced no chunks")
    chunk_flags: List[int] = [f for f in flags_by_seq if f is not None]
    all_rows: List[float] = [v for row in rows_by_seq if row is not None for v in row]

    # Check firmware health before trusting the data. error_code is the
    # authoritative post-measurement status byte; per-chunk flags are
    # undocumented but a non-zero value means the firmware flagged the chunk.
    status = await self.request_status()
    if status.error_code != 0:
      raise RuntimeError(
        f"{self.name} firmware error after read: "
        f"{self.describe_error_code(status.error_code)} "
        f"(chunk flags: {[f'0x{f:02x}' for f in chunk_flags]})"
      )
    self._warn_chunk_flags(chunk_flags)
    # The firmware streams the plate as one or more concatenated 96-well "tables"
    # (planes): seq_len chunks of 12 => seq_len / 8 tables. Some units return a
    # single plane (seq_len == 8), others return several (e.g. seq_len == 64 => 8
    # planes / 768 values). Only the first table is the reportable measurement —
    # this mirrors the vendor library, whose lum96_measure copies results[0] and
    # discards the rest (the extra planes are firmware-internal gain/diagnostic
    # data). Keep plane 0.
    if len(all_rows) == 0 or len(all_rows) % 96 != 0:
      raise RuntimeError(
        f"{self.name} luminescence read produced {len(all_rows)} values "
        f"(expected a positive multiple of 96)"
      )
    plane0 = all_rows[0:96]

    # Firmware zero-fills wells outside the mask. Convert those to None per
    # the LuminescenceResult contract ("None for unmeasured wells") — 0.0 is
    # a legitimate measurement (baseline subtraction can yield ~0 or negative).
    masked: List[Optional[float]] = [v if m else None for v, m in zip(plane0, mask_bools)]

    return [
      LuminescenceResult(
        data=reshape_2d(masked, (8, 12)),
        temperature=None,
        timestamp=datetime.now(),
      )
    ]


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
    model: Optional[str] = "Byonoy L96 Reader Unit",
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
        raise ValueError("ByonoyDriver can only have one plate holder assigned.")
      self.plate_holder._byonoy_base = self
    super().assign_child_resource(resource, location, reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    raise RuntimeError(
      "ByonoyDriver does not support assigning child resources directly. "
      "Use the plate_holder or reader_unit_holder to assign plates and the reader unit, "
      "respectively."
    )

  def serialize(self) -> dict:
    return {**Resource.serialize(self)}


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
