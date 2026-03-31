"""Plate type helpers for the Multidrop Combi.

Maps PyLabRobot Plate resources to Multidrop Combi plate type indices and
PLA (remote plate definition) command parameters.
"""

from __future__ import annotations

from pylabrobot.resources import Plate

# Multidrop Combi factory plate type definitions (from manual Table 3-3).
# Type index → (well_count, max_plate_height_mm)
# Heights are upper bounds for selecting the best-fit factory type.
_FACTORY_96_WELL_TYPES = [
  # (type_index, max_height_mm)
  (0, 18.0),  # Type 0: 96-well, 15mm
  (1, 30.0),  # Type 1: 96-well, 22mm
  (2, 55.0),  # Type 2: 96-well, 44mm
]

_FACTORY_384_WELL_TYPES = [
  (3, 8.5),  # Type 3: 384-well, 7.5mm
  (4, 12.0),  # Type 4: 384-well, 10mm
  (5, 18.0),  # Type 5: 384-well, 15mm
  (6, 30.0),  # Type 6: 384-well, 22mm
  (7, 55.0),  # Type 7: 384-well, 44mm
]

_FACTORY_1536_WELL_TYPES = [
  (8, 7.0),  # Type 8: 1536-well, 5mm
  (9, 55.0),  # Type 9: 1536-well, 10.5mm
]

# Hardware limits
MAX_COLUMNS = 48
MAX_ROWS = 32
MIN_HEIGHT_HUNDREDTHS_MM = 500  # 5mm
MAX_HEIGHT_HUNDREDTHS_MM = 5500  # 55mm
MAX_VOLUME_TENTHS_UL = 25000  # 2500 uL


def plate_to_type_index(plate: Plate) -> int:
  """Map a PLR Plate to the best-fit Multidrop Combi factory plate type index.

  Selects the factory type based on well count and plate height (size_z).
  The smallest factory type whose height threshold accommodates the plate is chosen.

  Args:
    plate: A PyLabRobot Plate resource.

  Returns:
    Factory plate type index (0-9).

  Raises:
    ValueError: If the plate well count is not 96, 384, or 1536, or if the
      plate height exceeds all factory type thresholds.
  """
  wells = plate.num_items
  height_mm = plate.get_size_z()

  if wells == 96:
    type_list = _FACTORY_96_WELL_TYPES
  elif wells == 384:
    type_list = _FACTORY_384_WELL_TYPES
  elif wells == 1536:
    type_list = _FACTORY_1536_WELL_TYPES
  else:
    raise ValueError(
      f"Unsupported well count: {wells}. "
      "Multidrop factory types support 96, 384, or 1536 wells. "
      "Use plate_to_pla_params() for custom plate definitions."
    )

  for type_index, max_height in type_list:
    if height_mm <= max_height:
      return type_index

  raise ValueError(
    f"Plate height {height_mm}mm exceeds all factory type thresholds for {wells}-well plates."
  )


def plate_to_pla_params(plate: Plate) -> dict:
  """Convert a PLR Plate to Multidrop Combi PLA command parameters.

  Use this for plates that don't match factory types (types 0-9), or when you
  want precise control over the plate definition sent to the instrument.
  The returned dict can be passed directly to ``backend.define_plate(**params)``.

  Args:
    plate: A PyLabRobot Plate resource.

  Returns:
    Dict with keys matching ``define_plate()`` parameters:
    column_positions, row_positions, rows, columns, height, max_volume.

  Raises:
    ValueError: If any parameter exceeds Multidrop hardware limits.
  """
  columns = plate.num_items_x
  rows = plate.num_items_y
  height_hundredths = round(plate.get_size_z() * 100)

  # Get max_volume from first well
  first_well = plate.get_well("A1")
  well_max_volume_tenths = round(first_well.max_volume * 10)

  # Validate against hardware limits
  if columns > MAX_COLUMNS:
    raise ValueError(f"Plate has {columns} columns, but Multidrop supports at most {MAX_COLUMNS}.")
  if rows > MAX_ROWS:
    raise ValueError(f"Plate has {rows} rows, but Multidrop supports at most {MAX_ROWS}.")
  if height_hundredths < MIN_HEIGHT_HUNDREDTHS_MM:
    raise ValueError(
      f"Plate height {plate.get_size_z()}mm is below minimum {MIN_HEIGHT_HUNDREDTHS_MM / 100}mm."
    )
  if height_hundredths > MAX_HEIGHT_HUNDREDTHS_MM:
    raise ValueError(
      f"Plate height {plate.get_size_z()}mm exceeds maximum {MAX_HEIGHT_HUNDREDTHS_MM / 100}mm."
    )
  if well_max_volume_tenths > MAX_VOLUME_TENTHS_UL:
    raise ValueError(
      f"Well max volume {first_well.max_volume} uL exceeds Multidrop limit of "
      f"{MAX_VOLUME_TENTHS_UL / 10} uL."
    )

  return {
    "column_positions": columns,
    "row_positions": rows,
    "rows": rows,
    "columns": columns,
    "height": height_hundredths,
    "max_volume": well_max_volume_tenths,
  }


def plate_well_count(plate: Plate) -> int:
  """Return the total well count for a plate."""
  return plate.num_items
