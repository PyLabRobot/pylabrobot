"""PLR labware conversion and bindings scoped to one Opentrons run."""

import math
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, cast

from pylabrobot import utils
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack

_OFFICIAL_TIP_RACKS = {
  "Opentrons OT-2 96 Filter Tip Rack 10 µL": "opentrons_96_filtertiprack_10ul",
  "Opentrons OT-2 96 Filter Tip Rack 20 µL": "opentrons_96_filtertiprack_20ul",
  "Opentrons OT-2 96 Filter Tip Rack 200 µL": "opentrons_96_filtertiprack_200ul",
  "Opentrons OT-2 96 Filter Tip Rack 1000 µL": "opentrons_96_filtertiprack_1000ul",
  "Opentrons OT-2 96 Tip Rack 10 µL": "opentrons_96_tiprack_10ul",
  "Opentrons OT-2 96 Tip Rack 20 µL": "opentrons_96_tiprack_20ul",
  "Opentrons OT-2 96 Tip Rack 300 µL": "opentrons_96_tiprack_300ul",
  "Opentrons OT-2 96 Tip Rack 1000 µL": "opentrons_96_tiprack_1000ul",
}


def official_tip_rack_identity(tip_rack: TipRack) -> Optional[LabwareIdentity]:
  """Look up a rack's official definition identity for its calibration data."""
  load_name = _OFFICIAL_TIP_RACKS.get(tip_rack.model or "")
  return LabwareIdentity("opentrons", load_name, 1) if load_name is not None else None


def build_tip_rack_definition(tip_rack: TipRack, tip: Tip, load_name: str) -> Dict[str, Any]:
  """Build a definition from PLR geometry without loading or modifying the rack."""
  tip_spots = tip_rack.get_all_items()
  well_names = {spot.name: tip_rack.get_child_identifier(spot) for spot in tip_spots}
  definition = {
    "schemaVersion": 2,
    "version": 1,
    "namespace": "pylabrobot",
    "metadata": {
      "displayName": load_name,
      "displayCategory": "tipRack",
      "displayVolumeUnits": "µL",
    },
    "brand": {"brand": "unknown"},
    "parameters": {
      "format": (
        "96Standard"
        if (tip_rack.num_items_x, tip_rack.num_items_y) == (12, 8)
        else "384Standard"
        if (tip_rack.num_items_x, tip_rack.num_items_y) == (24, 16)
        else "irregular"
      ),
      "isTiprack": True,
      "tipLength": tip.total_tip_length,
      "tipOverlap": tip.fitting_depth,
      "loadName": load_name,
      "isMagneticModuleCompatible": False,
    },
    "ordering": utils.reshape_2d(
      [well_names[tip_spot.name] for tip_spot in tip_spots],
      (tip_rack.num_items_x, tip_rack.num_items_y),
    ),
    "cornerOffsetFromSlot": {
      "x": 0,
      "y": 0,
      "z": 0,
    },
    "dimensions": {
      "xDimension": tip_rack.get_absolute_size_x(),
      "yDimension": tip_rack.get_absolute_size_y(),
      "zDimension": tip_rack.get_absolute_size_z(),
    },
    "wells": {
      well_names[child.name]: {
        "depth": tip.total_tip_length,
        "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
        "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
        "z": cast(Coordinate, child.location).z,
        "shape": "circular",
        "diameter": math.hypot(
          child.get_absolute_size_x(),
          child.get_absolute_size_y(),
        ),
        "totalLiquidVolume": tip.maximal_volume,
      }
      for child in tip_rack.children
    },
    "groups": [
      {
        "wells": [well_names[tip_spot.name] for tip_spot in tip_spots],
        "metadata": {},
      }
    ],
  }
  return definition


@dataclass(frozen=True)
class LabwareBinding:
  """A resource's server identity and location within one run."""

  resource: Resource
  labware_id: str
  slot: str
  identity: LabwareIdentity


class LabwareRegistry:
  """Record successful labware loads independently of the device's deck model."""

  def __init__(self, run: OpentronsRun) -> None:
    self._run = run
    self._bindings: Dict[int, LabwareBinding] = {}

  def is_loaded(self, resource: Resource) -> bool:
    return id(resource) in self._bindings

  def get(self, resource: Resource) -> LabwareBinding:
    """Look up an existing binding without uploading, loading, or allocating an ID."""
    return self._bindings[id(resource)]

  async def load(
    self,
    resource: Resource,
    slot: str,
    identity: LabwareIdentity,
    definition: Optional[Dict[str, Any]] = None,
  ) -> None:
    """Load a resource and record its binding after confirmed success.

    Repeated loads at the same slot are a no-op. A changed slot is rejected
    until an explicit server-side relocation has been implemented.
    """
    self._run._require_active()
    if self.is_loaded(resource):
      binding = self.get(resource)
      if slot != binding.slot:
        raise ValueError(
          f"Labware {resource.name!r} is loaded in slot {binding.slot}; "
          f"it cannot be used in slot {slot} during the same run"
        )
      if identity != binding.identity:
        raise ValueError(
          f"Labware {resource.name!r} already has a different definition in this run"
        )
      return

    if definition is not None:
      identity = await self._run.define_labware(definition)
    labware_id = uuid.uuid4().hex
    await self._run.load_labware(identity, slot, labware_id, resource.name)
    self._bindings[id(resource)] = LabwareBinding(resource, labware_id, slot, identity)
