"""The Mantis's parts, as resources, from the manufacturer's model.

Sizes and positions come from ``manifest.json``. Each part's ``mesh`` is its ``.glb``: mm, Z up,
in the part's own frame; the base's is the main box's frame. An arm link has no model file and is
drawn as its box. Each chip rack holds a chip spot per input slot; a chip spot holds one chip.
"""

import json
import math
import os
from typing import Any, Dict, Literal, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.rotation import Rotation

from .chip import (
  CHIP_SIZE_X,
  MANTIS_CHIP_FACTORIES,
  RFID_CHIP_TYPE_CODES,
  MantisChip,
  get_chip_factory,
  mantis_high_viscosity_continuous_flow_chip,
  mantis_high_volume_3pfe_chip,
  mantis_high_volume_chip,
  mantis_high_volume_hi_fi_chip,
  mantis_high_volume_pfe_chip,
  mantis_hv_continuous_flow_chip,
  mantis_low_volume_3pfe_chip,
  mantis_low_volume_chip,
  mantis_low_volume_hi_fi_chip,
  mantis_low_volume_pfe_chip,
  mantis_xl_volume_pfe_chip,
)

with open(os.path.join(os.path.dirname(__file__), "manifest.json"), encoding="utf-8") as f:
  # The device frame of ``manifest.json``: its size, base file and modules.
  MANIFEST: Dict[str, Any] = dict(json.load(f)["frames"]["Mantis"])


def _mesh(*, file: str) -> Dict[str, str]:
  """The ``mesh`` declaration for a model file in this folder."""
  return {"path": os.path.join(os.path.dirname(__file__), file), "units": "mm", "up": "Z"}


# Each input slot's rack, by slot.
CHIP_SLOT_RACKS: Dict[int, str] = {
  int(slot): entry["rack"] for slot, entry in MANIFEST["chip_slots"].items()
}


def chip_spot(name: str, *, rotation: Rotation) -> ResourceHolder:
  """A chip spot: one input slot of a chip rack, holding one chip.

  Its y runs from the slot's closed end toward its opening, and a chip sits centred across it with
  its back at the closed end.

  Args:
    name: the resource name.
    rotation: its rotation in its rack.
  """
  shape = MANIFEST["chip_slot"]
  return ResourceHolder(
    name=name,
    size_x=shape["width"],
    size_y=shape["length"],
    size_z=0.0,
    rotation=rotation,
    category="chip_spot",
    model="mantis_chip_spot",
    child_location=Coordinate((shape["width"] - CHIP_SIZE_X) / 2, 0.0, 0.0),
  )


def _get_chip_spot_placement(*, slot: int) -> Tuple[Coordinate, float]:
  """Where a slot's chip spot sits in the device frame, its closed end's corner at the floor's
  height above its rack's origin, and its rotation about z, in degrees."""
  shape = MANIFEST["chip_slot"]
  entry = MANIFEST["chip_slots"][str(slot)]
  end_x, end_y = entry["closed_end_centre"]
  axis = math.radians(entry["axis"])
  along = (math.cos(axis), math.sin(axis))
  across = (along[1], -along[0])  # the spot's x: its axis turned -90 degrees
  half_width = shape["width"] / 2
  corner = Coordinate(
    end_x - shape["end_radius"] * along[0] - half_width * across[0],
    end_y - shape["end_radius"] * along[1] - half_width * across[1],
    shape["floor"],
  )
  return corner, entry["axis"] - 90.0


def chip_rack(name: str, *, side: Literal["left", "right"]) -> Resource:
  """A chip rack, fitted on one side of the deck, with a chip spot for each of its input slots,
  named ``{name}_spot_{slot}``.

  Args:
    name: the resource name.
    side: "left" or "right", as you face the front.
  """
  part = MANIFEST["modules"][f"{side}_chip_rack"]
  size_x, size_y, size_z = part["size"]
  rack = Resource(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    category="chip_rack",
    model=part["file"][: -len(".glb")],
  )
  rack.mesh = _mesh(file=part["file"])  # type: ignore[attr-defined]
  rack_x, rack_y, _ = part["measured_at"]
  for slot, rack_side in CHIP_SLOT_RACKS.items():
    if rack_side != side:
      continue
    corner, angle = _get_chip_spot_placement(slot=slot)
    rack.assign_child_resource(
      chip_spot(name=f"{name}_spot_{slot}", rotation=Rotation(z=angle)),
      location=Coordinate(corner.x - rack_x, corner.y - rack_y, corner.z),
    )
  return rack


def z_column(name: str) -> Resource:
  """The Z column, which carries the arm.

  Args:
    name: the resource name.
  """
  part = MANIFEST["modules"]["z_column"]
  size_x, size_y, size_z = part["size"]
  column = Resource(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    category="z_column",
    model=part["file"][: -len(".glb")],
  )
  column.mesh = _mesh(file=part["file"])  # type: ignore[attr-defined]
  return column


def arm_link(name: str, *, link: str) -> LinkBody:
  """One of the arm's four links, drawn as its box. Motor 0 turns the right pair, motor 1 the left.

  Args:
    name: the resource name.
    link: which link: "right_short_arm",
      "right_long_arm", "left_short_arm" or "left_long_arm".
  """
  part = MANIFEST["arm_links"][link]
  size_x, size_y, size_z = part["size"]
  return LinkBody(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    proximal_joint=Coordinate(*part["proximal_joint"]),
    distal_joint=Coordinate(*part["distal_joint"]),
    category="mantis_arm_link",
    model=f"formulatrix_mantis_{link}",
  )


__all__ = [
  "CHIP_SLOT_RACKS",
  "MANIFEST",
  "MANTIS_CHIP_FACTORIES",
  "RFID_CHIP_TYPE_CODES",
  "MantisChip",
  "arm_link",
  "chip_rack",
  "chip_spot",
  "get_chip_factory",
  "mantis_high_viscosity_continuous_flow_chip",
  "mantis_high_volume_3pfe_chip",
  "mantis_high_volume_chip",
  "mantis_high_volume_hi_fi_chip",
  "mantis_high_volume_pfe_chip",
  "mantis_hv_continuous_flow_chip",
  "mantis_low_volume_3pfe_chip",
  "mantis_low_volume_chip",
  "mantis_low_volume_hi_fi_chip",
  "mantis_low_volume_pfe_chip",
  "mantis_xl_volume_pfe_chip",
  "z_column",
]
