"""EL406 plate type defaults and helper functions."""

from __future__ import annotations

from pylabrobot.resources import Plate

# Threshold for distinguishing standard-height vs low-profile plates (in mm).
# Standard microplates are ~14mm tall; PCR/flanged plates are typically <12mm.
_LOW_PROFILE_THRESHOLD_MM = 12.0

# Wire byte → physical defaults for each EL406 plate format.
# Keys are the raw byte values sent on the wire protocol.
_WIRE_BYTE_DEFAULTS: dict[int, dict[str, int]] = {
  0: {  # 1536-well standard
    "dispenser_height": 250,
    "dispense_z": 94,
    "aspirate_z": 42,
    "rows": 32,
    "cols": 48,
  },
  1: {  # 384-well standard
    "dispenser_height": 333,
    "dispense_z": 120,
    "aspirate_z": 22,
    "rows": 16,
    "cols": 24,
  },
  2: {  # 384-well PCR (low profile)
    "dispenser_height": 230,
    "dispense_z": 83,
    "aspirate_z": 2,
    "rows": 16,
    "cols": 24,
  },
  4: {  # 96-well
    "dispenser_height": 336,
    "dispense_z": 121,
    "aspirate_z": 29,
    "rows": 8,
    "cols": 12,
  },
  14: {  # 1536-well flanged (low profile)
    "dispenser_height": 196,
    "dispense_z": 93,
    "aspirate_z": 13,
    "rows": 32,
    "cols": 48,
  },
}


def plate_to_wire_byte(plate: Plate) -> int:
  """Resolve a PLR Plate to the EL406 wire protocol byte.

  Determines the format from well count, and uses plate height (``size_z``)
  to distinguish standard vs low-profile variants for 384 and 1536 plates.

  Args:
    plate: A PyLabRobot Plate resource.

  Returns:
    Integer byte value for the EL406 wire protocol.

  Raises:
    ValueError: If the plate well count is not 96, 384, or 1536.
  """
  wells = plate.num_items
  if wells == 96:
    return 4
  if wells == 384:
    return 2 if plate.get_size_z() < _LOW_PROFILE_THRESHOLD_MM else 1
  if wells == 1536:
    return 14 if plate.get_size_z() < _LOW_PROFILE_THRESHOLD_MM else 0
  raise ValueError(f"Unsupported plate well count: {wells}. EL406 supports 96, 384, or 1536.")


def plate_defaults(plate: Plate) -> dict[str, int]:
  """Return the physical defaults dict for a plate."""
  return _WIRE_BYTE_DEFAULTS[plate_to_wire_byte(plate)]


def plate_max_columns(plate: Plate) -> int:
  """Return the number of columns for a plate."""
  return plate.num_items_x


def plate_max_row_groups(plate: Plate) -> int:
  """Return the number of row groups for a plate.

  96-well: 1 row group (no row selection).
  384-well: 2 row groups.
  1536-well: 4 row groups.
  """
  return {12: 1, 24: 2, 48: 4}[plate.num_items_x]


def plate_well_count(plate: Plate) -> int:
  """Return the well count for a plate (96, 384, or 1536)."""
  return plate.num_items


def plate_default_z(plate: Plate) -> int:
  """Return the default dispenser Z height for a plate."""
  return plate_defaults(plate)["dispenser_height"]
