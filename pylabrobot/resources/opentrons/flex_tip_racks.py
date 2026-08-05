"""Flex tip rack definitions — thin, name-based labware.

Geometry here is **nominal**, not authoritative: a standard 96-position SBS
grid (127.76 x 85.48 mm footprint, 9 mm pitch) used only so PLR has named
``TipSpot``/``Tip`` objects to hang tip- and volume-tracking state on. The
*real* labware definition lives on the Flex robot itself — when a rack is
loaded, PLR sends the robot its Opentrons load name (``ot_load_name``) and
the robot resolves the authoritative geometry. Do not treat the coordinates
built here as measured/precise; they exist for addressing and tracking only.

Each factory function returns a PLR TipRack with:
- Standard TipSpots with TipTrackers for tip tracking and management
- Tips with VolumeTrackers for liquid volume tracking
- ``ot_load_name`` attribute for loading into the Flex robot's labware system
"""

from __future__ import annotations

from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d

# --- Nominal standard-96 SBS grid (addressing/tracking only) ---

_FOOTPRINT_X = 127.76  # standard SBS microplate footprint
_FOOTPRINT_Y = 85.48
_PITCH = 9.0  # center-to-center spacing, standard 96-well pitch

_NUM_COLS = 12
_NUM_ROWS = 8

# Symmetric nominal margins from the footprint edges to the A1 tip-spot
# center, derived from the standard footprint/pitch above (not measured).
_DX = (_FOOTPRINT_X - (_NUM_COLS - 1) * _PITCH) / 2  # 14.38
_DY = (_FOOTPRINT_Y - (_NUM_ROWS - 1) * _PITCH) / 2  # 11.24

_RACK_SIZE_Z = 99.0  # nominal rack height
_SPOT_SIZE = 5.5  # nominal tip-spot footprint


def _make_flex_tip_rack(
  name: str,
  ot_load_name: str,
  tip_volume: float,
  total_tip_length: float,
  fitting_depth: float,
  has_filter: bool = False,
) -> TipRack:
  """Create a PLR TipRack with a nominal 96-position grid.

  Returns a standard PLR TipRack with an extra ``ot_load_name``
  attribute identifying the Opentrons labware definition for the Flex robot.
  The grid geometry is nominal (see module docstring) — the Flex robot owns
  the authoritative definition, loaded by ``ot_load_name``.
  """

  def make_tip(name: str) -> Tip:
    return Tip(
      name=name,
      maximal_volume=tip_volume,
      total_tip_length=total_tip_length,
      fitting_depth=fitting_depth,
      has_filter=has_filter,
    )

  rack = TipRack(
    name=name,
    size_x=_FOOTPRINT_X,
    size_y=_FOOTPRINT_Y,
    size_z=_RACK_SIZE_Z,
    model=ot_load_name,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=_NUM_COLS,
      num_items_y=_NUM_ROWS,
      dx=_DX,
      dy=_DY,
      dz=0.0,
      item_dx=_PITCH,
      item_dy=_PITCH,
      size_x=_SPOT_SIZE,
      size_y=_SPOT_SIZE,
      make_tip=make_tip,
    ),
  )

  # Flex-specific: Opentrons labware load name for JIT loading. The robot
  # resolves the real geometry from this name; PLR's grid above is nominal.
  rack.ot_load_name = ot_load_name  # type: ignore[attr-defined]

  return rack


# --- Tip Rack Factory Functions ---


def flex_96_tiprack_50ul(name: str = "flex_96_tiprack_50ul") -> TipRack:
  """Opentrons Flex 96 Tip Rack 50 µL.

  Tip length 57.9mm, fitting depth 10.5mm (from Opentrons specs).
  """
  return _make_flex_tip_rack(
    name=name,
    ot_load_name="opentrons_flex_96_tiprack_50ul",
    tip_volume=50.0,
    total_tip_length=57.9,
    fitting_depth=10.5,
  )


def flex_96_filtertiprack_50ul(
  name: str = "flex_96_filtertiprack_50ul",
) -> TipRack:
  """Opentrons Flex 96 Filter Tip Rack 50 µL.

  Physically identical geometry to ``flex_96_tiprack_50ul`` (same 96-well
  layout, tip length 57.9mm, fitting depth 10.5mm) — the only difference is the
  aerosol filter, so it is the same rack with ``has_filter=True`` and the
  Opentrons filter load name. Lets a protocol that uses filter tips resolve
  against the resource model.
  """
  return _make_flex_tip_rack(
    name=name,
    ot_load_name="opentrons_flex_96_filtertiprack_50ul",
    tip_volume=50.0,
    total_tip_length=57.9,
    fitting_depth=10.5,
    has_filter=True,
  )


def flex_96_tiprack_200ul(name: str = "flex_96_tiprack_200ul") -> TipRack:
  """Opentrons Flex 96 Tip Rack 200 µL."""
  return _make_flex_tip_rack(
    name=name,
    ot_load_name="opentrons_flex_96_tiprack_200ul",
    tip_volume=200.0,
    total_tip_length=58.35,
    fitting_depth=10.5,
  )


def flex_96_tiprack_1000ul(name: str = "flex_96_tiprack_1000ul") -> TipRack:
  """Opentrons Flex 96 Tip Rack 1000 µL."""
  return _make_flex_tip_rack(
    name=name,
    ot_load_name="opentrons_flex_96_tiprack_1000ul",
    tip_volume=1000.0,
    total_tip_length=95.6,
    fitting_depth=10.5,
  )
