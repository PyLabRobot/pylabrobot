"""Resource trees built with PLR's `Resource` classes, whose answers the store must give too.

Each scene is a whole tree as a device would hold it: carriers on tracks, plates, lids, stacks,
adapters, tip racks with tips taken, troughs, tubes, a head's channels with tips on them, a
gripper, and frames turned by quarters, by odd angles, and about x and y.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Callable, Dict, List

from pylabrobot.hamilton.star.resource_model.heads import head96
from pylabrobot.hamilton.star.resource_model.iswap import iswap_gripper, iswap_link_1
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  ResourceHolder,
  ResourceStack,
  Rotation,
  TipRack,
  Well,
  create_ordered_items_2d,
)
from pylabrobot.resources.celltreat.plates import celltreat_96_wellplate_350uL_Ub
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.eppendorf.tubes import eppendorf_tube_1500uL_Vb
from pylabrobot.resources.hamilton import (
  HamiltonSTARDeck,
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_plate_carrier_L5_ac,
  hamilton_tip_carrier_L5,
)
from pylabrobot.resources.hamilton.mfx_carriers import hamilton_mfx_carrier_L5_base
from pylabrobot.resources.hamilton.mfx_modules import (
  hamilton_mfx_plateholder_DWP_flat,
  hamilton_mfx_resourceholder_ntr,
)
from pylabrobot.resources.hamilton.trough_carriers import Trough_CAR_4R200_A00
from pylabrobot.resources.hamilton.troughs import hamilton_1_trough_200mL_Vb
from pylabrobot.resources.hamilton.tube_carriers import hamilton_tube_carrier_12_b00
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.thermo_fisher.plate_adapters import thermo_AB_96_plateadapter_MicroAmp
from pylabrobot.resources.thermo_fisher.plates import thermo_AB_96_wellplate_300ul_Vb_MicroAmp


@dataclass
class Scene:
  """A tree, and what the cases built on it need to know about it.

  Attributes:
    name: what to call it.
    root: the tree.
    frame: the resource commands measure from, the deck.
  """

  name: str
  root: Resource
  frame: Resource


def stacking_plate(name: str, stacking_z_height: float = 4.0) -> Plate:
  """A small plate that nests into another by its stacking height."""
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.5,
    stacking_z_height=stacking_z_height,
    ordered_items=create_ordered_items_2d(
      Well,
      name_prefix=name,
      num_items_x=3,
      num_items_y=2,
      dx=10.0,
      dy=7.0,
      dz=1.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=12.0,
      material_z_thickness=0.8,
      max_volume=300,
    ),
  )


def _filled_deck(name: str = "deck", tip_racks: bool = True) -> HamiltonSTARDeck:
  """A STARlet deck with one of most things on it; its tip racks left off when not wanted."""
  deck = STARLetDeck(name=name)

  plates = hamilton_plate_carrier_L5_ac(name="plate_carrier")
  plates[0] = cor_96_wellplate_360uL_Fb(name="plate")
  plates[1] = celltreat_96_wellplate_350uL_Ub(name="lidded_plate", with_lid=True)
  stack = ResourceStack("plate_stack", "z")
  plates[2] = stack
  stack.assign_child_resource(stacking_plate("stacked_1"))
  stack.assign_child_resource(stacking_plate("stacked_2"))
  stack.assign_child_resource(stacking_plate("stacked_3"))
  adapter = thermo_AB_96_plateadapter_MicroAmp(name="adapter")
  plates[3] = adapter
  adapter.assign_child_resource(thermo_AB_96_wellplate_300ul_Vb_MicroAmp(name="pcr_plate"))
  deck.assign_child_resource(plates, track=1)

  troughs = Trough_CAR_4R200_A00(name="trough_carrier")
  troughs[0] = hamilton_1_trough_200mL_Vb(name="trough_0")
  troughs[2] = hamilton_1_trough_200mL_Vb(name="trough_2")
  deck.assign_child_resource(troughs, track=15)

  tubes = hamilton_tube_carrier_12_b00(name="tube_carrier")
  tubes[0] = eppendorf_tube_1500uL_Vb(name="tube_0")
  tubes[5] = eppendorf_tube_1500uL_Vb(name="tube_5")
  deck.assign_child_resource(tubes, track=21)

  if not tip_racks:
    return deck

  tips = hamilton_tip_carrier_L5(name="tip_carrier")
  tips[0] = hamilton_96_tiprack_300uL_filter(name="tips_full")
  partial = hamilton_96_tiprack_300uL_filter(name="tips_partial")
  tips[1] = partial
  for identifier in ("A1", "B1", "C1", "H12"):
    partial.get_item(identifier).unassign_tip()
  deck.assign_child_resource(tips, track=8)

  ntr = hamilton_mfx_resourceholder_ntr(name="ntr_site")
  mfx = hamilton_mfx_carrier_L5_base(
    name="mfx_carrier",
    modules={0: hamilton_mfx_plateholder_DWP_flat(name="mfx_plate_site"), 2: ntr},
  )
  mfx[0] = cor_96_wellplate_360uL_Fb(name="mfx_plate")
  ntr_stack = ResourceStack("ntr_stack", "z")
  ntr.assign_child_resource(ntr_stack)
  ntr_stack.assign_child_resource(hamilton_96_tiprack_300uL_NTR(name="ntr_1", with_tips=False))
  ntr_stack.assign_child_resource(hamilton_96_tiprack_300uL_NTR(name="ntr_2", with_tips=False))
  deck.assign_child_resource(mfx, track=24)
  return deck


def turned_deck() -> Scene:
  """A deck on a device turned half round, with carriers and plates turned by quarters on it."""
  device = Resource("turned_device", size_x=1200, size_y=800, size_z=900, rotation=Rotation(z=180))
  deck = _filled_deck(tip_racks=False)
  device.assign_child_resource(deck, location=Coordinate(1100, 700, 50))

  turned = hamilton_plate_carrier_L5_ac(name="turned_carrier")
  turned.rotation = Rotation(z=90)
  deck.assign_child_resource(turned, location=Coordinate(900, 50, 100), ignore_collision=True)
  turned[0] = cor_96_wellplate_360uL_Fb(name="turned_plate")

  site = ResourceHolder(
    "quarter_site", size_x=127.76, size_y=85.48, size_z=5, child_location=Coordinate(1, 2, 3)
  )
  deck.assign_child_resource(site, location=Coordinate(700, 400, 150), ignore_collision=True)
  quarter = cor_96_wellplate_360uL_Fb(name="quarter_plate")
  quarter.rotation = Rotation(z=270)
  site.assign_child_resource(quarter)
  return Scene("turned_deck", device, deck)


def tilted() -> Scene:
  """Frames turned by odd angles about every axis, nested."""
  root = Resource("tilted_root", size_x=500, size_y=500, size_z=500)
  outer = Resource(
    "tilted_outer", size_x=300, size_y=200, size_z=100, rotation=Rotation(x=10, y=-20, z=33)
  )
  root.assign_child_resource(outer, location=Coordinate(50, 60, 70))
  inner = ResourceHolder(
    "tilted_holder", size_x=130, size_y=90, size_z=10, rotation=Rotation(z=17.5, x=-5)
  )
  outer.assign_child_resource(inner, location=Coordinate(20, 30, 40))
  inner.assign_child_resource(cor_96_wellplate_360uL_Fb(name="tilted_plate"))
  lidded = celltreat_96_wellplate_350uL_Ub(name="tilted_lidded", with_lid=True)
  lidded.rotation = Rotation(y=180)
  outer.assign_child_resource(lidded, location=Coordinate(150, 10, 5))
  return Scene("tilted", root, root)


def star_deck() -> Scene:
  """A STARlet deck as set up for a run, with what moves over it: an arm carrying channels with
  tips on them, a 96-head, and an iSWAP link with its gripper."""
  deck = _filled_deck()
  arm = Resource("x_arm", size_x=30, size_y=650, size_z=40, category="x_arm")
  deck.assign_child_resource(arm, location=Coordinate(400, 0, 250), ignore_collision=True)
  rack = deck.get_resource("tips_full")
  assert isinstance(rack, TipRack)
  for channel in range(4):
    body = Resource(f"channel_{channel}", size_x=9, size_y=9, size_z=100, category="channel")
    arm.assign_child_resource(body, location=Coordinate(10, 400 - 9 * channel, 20))
    shaft = TipMountingShaft(name=f"channel_{channel}_shaft", tip_pickup_mode="core")
    body.assign_child_resource(
      shaft,
      location=Coordinate(
        (body.get_size_x() - shaft.get_size_x()) / 2,
        (body.get_size_y() - shaft.get_size_y()) / 2,
        -shaft.get_size_z(),
      ),
    )
    if channel < 2:
      shaft.mount_tip(rack.get_item(channel).tip_for_pickup())

  head = head96("head96", size_z=40)
  arm.assign_child_resource(head, location=Coordinate(-60, 100, 30))

  link = iswap_link_1("iswap_link_1", length=120)
  arm.assign_child_resource(link, location=Coordinate(0, 500, -60))
  gripper = iswap_gripper(
    "iswap_gripper", tool_center_point=Coordinate(80, 0, -20), jaw_range=(60.0, 140.0)
  )
  link.assign_child_resource(gripper, location=Coordinate(100, 0, -10))
  return Scene("star_deck", deck, deck)


SCENES: Dict[str, Callable[[], Scene]] = {
  "star_deck": star_deck,
  "turned_deck": turned_deck,
  "tilted": tilted,
}


def build_all() -> List[Scene]:
  """Every scene, built fresh, with PLR's deprecation warnings kept quiet."""
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    return [build() for build in SCENES.values()]
