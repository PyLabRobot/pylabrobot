"""The Mantis's parts, as resources, from the manufacturer's model.

Sizes and positions come from ``manifest.json``. Each part's ``mesh`` is its ``.glb``: mm, Z up,
in the part's own frame; the base's is the main box's frame. An arm link has no model file and is
drawn as its box.
"""

import json
import os
from typing import Any, Dict, Literal

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource

with open(os.path.join(os.path.dirname(__file__), "manifest.json"), encoding="utf-8") as f:
  # The device frame of ``manifest.json``: its size, base file and modules.
  MANIFEST: Dict[str, Any] = dict(json.load(f)["frames"]["Mantis"])


def _mesh(*, file: str) -> Dict[str, str]:
  """The ``mesh`` declaration for a model file in this folder."""
  return {"path": os.path.join(os.path.dirname(__file__), file), "units": "mm", "up": "Z"}


def chip_rack(name: str, *, side: Literal["left", "right"]) -> Resource:
  """A chip rack, fitted on one side of the deck.

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


__all__ = ["MANIFEST", "arm_link", "chip_rack", "z_column"]
