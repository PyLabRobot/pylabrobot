"""PLR labware conversion and bindings scoped to one Opentrons run."""

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

from pylabrobot.opentrons.labware_definitions import (
  build_tip_rack_definition as build_custom_tip_rack_definition,
)
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
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


def declared_labware_identity(resource: Resource) -> Optional[LabwareIdentity]:
  """Read the explicit, serialized identity chosen for a resource."""
  metadata = resource.metadata.get("opentrons_labware")
  if metadata is None:
    return None
  if not isinstance(metadata, dict):
    raise ValueError("opentrons_labware metadata must be an object")
  namespace, load_name, version = (
    metadata.get(key) for key in ("namespace", "load_name", "version")
  )
  if (
    not isinstance(namespace, str)
    or not namespace
    or not isinstance(load_name, str)
    or not load_name
  ):
    raise ValueError("Opentrons labware namespace and load_name must be non-empty strings")
  if not isinstance(version, int) or isinstance(version, bool) or version < 1:
    raise ValueError("Opentrons labware version must be a positive integer")
  return LabwareIdentity(namespace, load_name, version)


def build_tip_rack_definition(tip_rack: TipRack, tip: Tip, load_name: str) -> Dict[str, Any]:
  """Build an OT-2 rack definition with its existing tip-spot diameter convention."""
  definition = build_custom_tip_rack_definition(tip_rack, tip=tip, load_name=load_name)
  definition["metadata"]["displayName"] = load_name
  definition["groups"][0]["metadata"] = {}
  for spot in tip_rack.get_all_items():
    definition["wells"][tip_rack.get_child_identifier(spot)]["diameter"] = math.hypot(
      spot.get_absolute_size_x(), spot.get_absolute_size_y()
    )
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

  def __init__(self, run: OpentronsRun, server_assigned_ids: bool = False) -> None:
    self._run = run
    self._bindings: Dict[int, LabwareBinding] = {}
    self._definitions: Dict[int, Tuple[Resource, LabwareIdentity]] = {}
    self._server_assigned_ids = server_assigned_ids

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
      identity = await self.define(resource, definition)
    if self._server_assigned_ids:
      labware_id = await self._run.load_labware(identity, slot, None, resource.name)
    else:
      labware_id = uuid.uuid4().hex
      await self._run.load_labware(identity, slot, labware_id, resource.name)
    self._bindings[id(resource)] = LabwareBinding(resource, labware_id, slot, identity)

  async def define(self, resource: Resource, definition: Dict[str, Any]) -> LabwareIdentity:
    """Upload a resource definition once per run, retaining its server identity."""
    self._run._require_active()
    cached = self.definition(resource)
    if cached is not None:
      return cached
    identity = await self._run.define_labware(definition)
    self._definitions[id(resource)] = (resource, identity)
    return identity

  def definition(self, resource: Resource) -> Optional[LabwareIdentity]:
    """Return a previously uploaded definition without communicating."""
    cached = self._definitions.get(id(resource))
    return cached[1] if cached is not None else None

  def record_location(self, resource: Resource, slot: str) -> None:
    """Update a binding after a confirmed server-side move."""
    self._bindings[id(resource)] = replace(self.get(resource), slot=slot)

  def remove(self, resource: Resource) -> None:
    """Forget a resource after its confirmed departure from the run's deck."""
    self._bindings.pop(id(resource), None)
    self._definitions.pop(id(resource), None)
