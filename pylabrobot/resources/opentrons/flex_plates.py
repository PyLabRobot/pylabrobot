"""Flex plate definitions — thin, name-based labware.

Geometry here is **nominal**, not authoritative: a standard 96-position SBS
grid (127.76 x 85.48 mm footprint, 9 mm pitch) used only so PLR has named
``Well`` objects to hang volume-tracking state on. The *real* labware
definition lives on the Flex robot itself — when a plate is loaded, PLR sends
the robot its Opentrons load name (``ot_load_name``) and the robot resolves
the authoritative geometry. Do not treat the coordinates built here as
measured/precise; they exist for addressing and tracking only.

``flex_plate(load_name, name, ...)`` builds a plate for ANY Opentrons plate
load name — pick the load name from the Opentrons Labware Library and PLR
will build a same-shaped nominal grid to track it. ``corning_96_wellplate_360ul_flat``
is a convenience wrapper for the plate used in the hello-world notebook.
"""

from __future__ import annotations

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType

# --- Nominal standard-96 SBS grid (addressing/tracking only) ---

_FOOTPRINT_X = 127.76  # standard SBS microplate footprint
_FOOTPRINT_Y = 85.48
_PITCH = 9.0  # center-to-center spacing, standard 96-well pitch

_NUM_COLS = 12
_NUM_ROWS = 8

# Symmetric nominal margins from the footprint edges to the A1 well center,
# derived from the standard footprint/pitch above (not measured).
_DX = (_FOOTPRINT_X - (_NUM_COLS - 1) * _PITCH) / 2  # 14.38
_DY = (_FOOTPRINT_Y - (_NUM_ROWS - 1) * _PITCH) / 2  # 11.24

_PLATE_SIZE_Z = 14.5  # nominal plate height
_WELL_SIZE = 6.4  # nominal well footprint (round well diameter)
_WELL_SIZE_Z = 10.9  # nominal well depth


def flex_plate(
  load_name: str,
  name: str,
  num_wells: int = 96,
  well_volume: float = 360.0,
  version: int = 1,
) -> Plate:
  """Build a nominal, name-based Flex plate.

  Args:
    load_name: the Opentrons labware load name (e.g.
      ``"corning_96_wellplate_360ul_flat"``) sent to the Flex robot when this
      plate is loaded — the robot resolves the authoritative geometry from
      this name. Stored on the returned ``Plate`` as ``ot_load_name``.
    name: the PLR resource name for this plate instance.
    num_wells: number of wells; only the standard 96-well SBS grid (8 rows x
      12 columns) is supported today.
    well_volume: nominal per-well max volume (uL), used for volume tracking.
    version: which revision of that definition to load. Stored as
      ``ot_version``. Revision 1 is the one every definition has, and the only
      one safe to assume, but for much of Opentrons' catalogue it predates the
      Flex and states no gripper grip height, so the robot grips at the plate's
      mid-height. A later revision usually fixes that and leaves the well
      geometry alone. Naming a revision the robot does not hold fails the load,
      so raise this only for a plate you know the robot has.

  Returns:
    A PLR ``Plate`` with a nominal 96-well grid (see module docstring) and
    ``ot_load_name`` set to ``load_name``.
  """
  if num_wells != 96:
    raise ValueError(
      f"flex_plate only supports the standard 96-well SBS grid today; got num_wells={num_wells}."
    )

  plate = Plate(
    name=name,
    size_x=_FOOTPRINT_X,
    size_y=_FOOTPRINT_Y,
    size_z=_PLATE_SIZE_Z,
    model=load_name,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=_NUM_COLS,
      num_items_y=_NUM_ROWS,
      dx=_DX,
      dy=_DY,
      dz=0.0,
      item_dx=_PITCH,
      item_dy=_PITCH,
      size_x=_WELL_SIZE,
      size_y=_WELL_SIZE,
      size_z=_WELL_SIZE_Z,
      bottom_type=WellBottomType.FLAT,
      max_volume=well_volume,
    ),
  )

  # Flex-specific: Opentrons labware load name for JIT loading. The robot
  # resolves the real geometry from this name; PLR's grid above is nominal.
  plate.ot_load_name = load_name  # type: ignore[attr-defined]
  plate.ot_version = version  # type: ignore[attr-defined]

  return plate


def corning_96_wellplate_360ul_flat(name: str) -> Plate:
  """Corning 96-well flat-bottom plate, 360 uL wells — Opentrons Labware Library name.

  Convenience wrapper around :func:`flex_plate` for
  ``"corning_96_wellplate_360ul_flat"``, the plate used in the Flex
  hello-world notebook.

  Loads revision 2, the earliest that states a gripper grip height (12.2 mm);
  revision 1 states none and the robot grips at the plate's mid-height instead.
  The well geometry is the same in both.
  """
  return flex_plate(
    load_name="corning_96_wellplate_360ul_flat",
    name=name,
    num_wells=96,
    well_volume=360.0,
    version=2,
  )
