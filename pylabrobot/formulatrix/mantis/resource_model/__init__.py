"""The Mantis's parts, as resources, from the manufacturer's model.

Sizes and positions come from ``manifest.json``. Each part's ``mesh`` is its ``.glb``: mm, Z up,
in the part's own frame; the base's is the main box's frame.
"""

import json
import os
from typing import Any, Dict, Literal

from pylabrobot.resources.coordinate import Coordinate
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


__all__ = ["MANIFEST", "chip_rack"]
