"""Flex tip rack definitions — uses PLR's TipRack/TipSpot/Tip classes.

Tip positions are hardcoded from the Opentrons labware definition JSON
(downloaded from the Flex robot on 2026-04-01). Standard 96-well layout
with 9mm pitch.

Each factory function returns a PLR TipRack with:
- Standard TipSpots with TipTrackers for tip tracking and management
- Tips with VolumeTrackers for liquid volume tracking
- ``ot_load_name`` attribute for loading into the Flex robot's labware system
"""

from __future__ import annotations

from typing import Dict

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot

# --- Standard 96-well positions (9mm pitch, from Opentrons JSON) ---

# A1 at (14.38, 74.38), rows go down in Y, columns go right in X.
# Z = 1.5mm (base of tip pocket).
_WELL_DX = 14.38  # left margin to A1 center
_WELL_DY = 74.38  # bottom margin to A1 center (measured from front)
_WELL_DZ = 1.5  # z offset
_PITCH = 9.0  # center-to-center spacing

# Tip spot size (diameter of the tip pocket)
_SPOT_SIZE = 5.58


def _make_flex_tip_rack(
  name: str,
  ot_load_name: str,
  tip_volume: float,
  total_tip_length: float,
  fitting_depth: float,
  has_filter: bool = False,
) -> TipRack:
  """Create a PLR TipRack with 96 positions in Flex geometry.

  Returns a standard PLR TipRack with an extra ``ot_load_name``
  attribute identifying the Opentrons labware definition for the Flex robot.
  """

  def make_tip(name: str) -> Tip:
    return Tip(
      name=name,
      maximal_volume=tip_volume,
      total_tip_length=total_tip_length,
      fitting_depth=fitting_depth,
      has_filter=has_filter,
    )

  # Create ordered_items dict: {"A1": TipSpot, "B1": TipSpot, ...}
  # Column-major order (A1, B1, ..., H1, A2, B2, ..., H12)
  ordered_items: Dict[str, TipSpot] = {}
  for col_idx in range(12):
    for row_idx, row_letter in enumerate("ABCDEFGH"):
      identifier = f"{row_letter}{col_idx + 1}"
      spot = TipSpot(
        name=identifier,
        size_x=_SPOT_SIZE,
        size_y=_SPOT_SIZE,
        make_tip=make_tip,
      )
      spot.location = Coordinate(
        x=_WELL_DX + col_idx * _PITCH,
        y=_WELL_DY - row_idx * _PITCH,
        z=_WELL_DZ,
      )
      ordered_items[identifier] = spot

  rack = TipRack(
    name=name,
    size_x=127.75,
    size_y=85.75,
    size_z=99.0,
    ordered_items=ordered_items,
    model=ot_load_name,
  )

  # Flex-specific: Opentrons labware load name for JIT loading
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
