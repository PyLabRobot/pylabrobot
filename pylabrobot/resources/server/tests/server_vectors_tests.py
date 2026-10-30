"""The shared geometry vectors, asked of a store through its command API.

Each case's tree is created in the store, and every question in the case is asked as PLR asks it
of a store: locations, sizes and rotations through `ResolveGeometry`; heights and volumes through
`ConvertHeightVolume`; what is at a point through `FindResourcesAt`; where a parent seats a child
through `AssignResources`; turns through `UpdateResources`; jaws through `RecordDevicePositions`;
placements and grips through `BeginResourcePlace` and `BeginResourcePickup`, rolled back after.

Where PLR refuses, the store must refuse. Where the store's rules refuse what PLR's geometry
answered - a site already holding something, a plate that takes only lids - the case is counted as
ruled out, and enough must be left to check.
"""

from __future__ import annotations

import unittest
from typing import Any, Callable, Dict, List, Tuple

from google.protobuf import json_format

from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tests import vectors
from pylabrobot.resources.server.tests.server_tests import ServerTestCase
from pylabrobot.resources.server.tree import to_rotation
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb

VECTORS = vectors.load()


def vec(entry: Dict[str, float]) -> pb.Vec3:
  return pb.Vec3(x=entry.get("x", 0.0), y=entry.get("y", 0.0), z=entry.get("z", 0.0))


class ServerVectorTestCase(ServerTestCase):
  """A store holding one case's tree, and nothing else."""

  case: Dict[str, Any]

  async def asyncSetUp(self) -> None:
    self.server = self.make_server()
    self.commands = self.server.commands
    tree = json_format.ParseDict(self.case["tree"], pb.ResourceDefinition())
    self.root = tree.attributes.name
    await self.commands.create_resources(
      spb.CreateResourcesRequest(resources=[spb.CreateResource(definition=tree)])
    )
    self.wrong: List[str] = []

  def ref(self, name: str) -> pb.ResourceRef:
    if name == self.root:
      return pb.ResourceRef(name=pb.NameRef(name=name))
    return pb.ResourceRef(name=pb.NameRef(root=self.root, name=name))

  def expect(self, what: str, entry: Dict[str, Any], key: str, ours: Any, same) -> None:
    if entry.get("refused"):
      self.wrong.append(f"{what}: answered {ours}, expected a refusal")
    elif not same(ours, entry[key]):
      self.wrong.append(f"{what}: {ours} != {entry[key]}")

  def check(self) -> None:
    self.assertEqual(self.wrong, [], "\n".join(self.wrong[:20]))

  async def named(self, name: str) -> pb.ResourceNode:
    response = await self.commands.get_resources(
      spb.GetResourcesRequest(resources=[self.ref(name)], depth=0)
    )
    node: pb.ResourceNode = response.nodes[0]
    return node

  # -- read only ---------------------------------------------------------------------------------

  async def check_locations(self) -> None:
    entries = self.case["locations"]
    queries = []
    for entry in entries:
      query = spb.GeometryQuery(
        resource=self.ref(entry["resource"]),
        anchors=[json_format.ParseDict(entry["anchor"], pb.Anchor())],
      )
      if "frame" in entry:
        query.frame.CopyFrom(self.ref(entry["frame"]))
      queries.append(query)
    response = await self.commands.resolve_geometry(spb.ResolveGeometryRequest(queries=queries))
    for entry, result in zip(entries, response.results):
      what = f"{entry['resource']} at {entry['anchor']} in {entry.get('frame', 'its root')}"
      if result.HasField("issue"):
        if not entry.get("refused"):
          self.wrong.append(f"{what}: refused ({result.issue.message})")
        continue
      location = result.locations[0]
      self.expect(what, entry, "location", (location.x, location.y, location.z), vectors.close_vec)

  async def check_resources(self) -> None:
    entries = self.case["resources"]
    response = await self.commands.resolve_geometry(
      spb.ResolveGeometryRequest(
        queries=[
          spb.GeometryQuery(resource=self.ref(e["resource"]), frame=self.ref(self.case["frame"]))
          for e in entries
        ]
      )
    )
    for entry, result in zip(entries, response.results):
      name = entry["resource"]
      size, absolute = result.size, result.absolute_size
      checks: List[Tuple[str, Any, Callable[[Any, Any], bool]]] = [
        ("size", (size.x, size.y, size.z), vectors.close_vec),
        ("absoluteSize", (absolute.x, absolute.y, absolute.z), vectors.close_vec),
        ("absoluteRotation", to_rotation(result.absolute_rotation), vectors.close_angles),
        ("rotationInFrame", to_rotation(result.rotation_in_frame), vectors.close_angles),
        ("center", (size.x / 2, size.y / 2, 0.0), vectors.close_vec),
      ]
      if "highestPoint" in entry:
        checks.append(("highestPoint", result.highest_point, vectors.close))
      for key, ours, same in checks:
        if not same(ours, entry[key]):
          self.wrong.append(f"{name} {key}: {ours} != {entry[key]}")

  async def check_height_volume(self) -> None:
    entries = self.case["heightVolume"]
    queries = [
      spb.HeightVolumeQuery(container=self.ref(e["resource"]), height=e["height"])
      if "height" in e
      else spb.HeightVolumeQuery(container=self.ref(e["resource"]), volume=e["volume"])
      for e in entries
    ]
    response = await self.commands.convert_height_volume(
      spb.ConvertHeightVolumeRequest(queries=queries)
    )
    for entry, result in zip(entries, response.results):
      what = f"{entry['resource']} at {entry.get('height', entry.get('volume'))}"
      if result.HasField("issue"):
        if not entry.get("refused"):
          self.wrong.append(f"{what}: refused ({result.issue.message})")
        continue
      ours = result.volume if "height" in entry else result.height
      self.expect(what, entry, "result", ours, vectors.close)

  async def check_occupancy(self) -> None:
    for entry in self.case["occupancy"]:
      response = await self.commands.find_resources_at(
        spb.FindResourcesAtRequest(
          frame=self.ref(self.case["frame"]),
          location=vec(entry["location"]),
          exclude=[self.ref(name) for name in entry["exclude"]],
        )
      )
      found = response.nodes[0].attributes.name if response.nodes else None
      if found != entry["found"]:
        self.wrong.append(f"at {entry['location']}: {found} != {entry['found']}")

  # -- changes, each undone ----------------------------------------------------------------------

  async def add_arm(self) -> None:
    """Hang an arm over the frame, out of everything's way, to carry what is gripped."""
    arm = pb.ResourceDefinition(
      attributes=pb.ResourceAttributes(
        name="probe_arm",
        kind=pb.RESOURCE_KIND_RESOURCE,
        category="x_arm",
        size=pb.Vec3(x=1, y=1, z=1),
      )
    )
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            definition=arm,
            assign=pb.AssignChild(
              parent=self.ref(self.case["frame"]), location=pb.Vec3(x=0, y=0, z=900)
            ),
          )
        ]
      )
    )

  async def check_seats(self) -> int:
    checked = 0
    for entry in self.case["seats"]:
      child = await self.named(entry["child"])
      parent_id = child.parent_id
      try:
        moved = await self.commands.assign_resources(
          spb.AssignResourcesRequest(
            assignments=[
              pb.AssignChild(child=self.ref(entry["child"]), parent=self.ref(entry["parent"]))
            ]
          )
        )
      except ServerError:
        continue
      location = moved.nodes[0].attributes.location
      self.expect(
        f"{entry['child']} seated in {entry['parent']}",
        entry,
        "location",
        (location.x, location.y, location.z),
        vectors.close_vec,
      )
      checked += 1
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[
            pb.AssignChild(
              child=self.ref(entry["child"]),
              parent=pb.ResourceRef(id=parent_id),
              location=child.attributes.location,
              index=child.index,
            )
          ]
        )
      )
    return checked

  async def check_turns(self) -> None:
    for entry in self.case["turns"]:
      before = await self.named(entry["resource"])
      change = pb.RotationChange(resource=self.ref(entry["resource"]))
      if "by" in entry:
        change.by.CopyFrom(json_format.ParseDict(entry["by"], pb.Rotation()))
      else:
        change.to.CopyFrom(json_format.ParseDict(entry["to"], pb.PartialRotation()))
      if "pivot" in entry:
        change.pivot.CopyFrom(vec(entry["pivot"]))
      response = await self.commands.update_resources(
        spb.UpdateResourcesRequest(updates=[spb.ResourceUpdate(rotation=change)])
      )
      turned = response.nodes[0].attributes
      if not vectors.close_angles(to_rotation(turned.rotation), entry["rotation"]):
        self.wrong.append(f"{entry['resource']} turned: {turned.rotation} != {entry['rotation']}")
      at = turned.location
      if not vectors.close_vec((at.x, at.y, at.z), entry["location"]):
        self.wrong.append(f"{entry['resource']} turned to {at}, expected {entry['location']}")
      was = before.attributes.rotation
      await self.commands.update_resources(
        spb.UpdateResourcesRequest(
          updates=[
            spb.ResourceUpdate(
              rotation=pb.RotationChange(
                resource=self.ref(entry["resource"]),
                to=pb.PartialRotation(x=was.x, y=was.y, z=was.z),
              )
            )
          ]
        )
      )
      await self.commands.record_device_positions(
        spb.RecordDevicePositionsRequest(
          positions=[
            spb.DevicePosition(
              resource=self.ref(entry["resource"]),
              location=pb.PartialVec3(
                x=before.attributes.location.x,
                y=before.attributes.location.y,
                z=before.attributes.location.z,
              ),
            )
          ]
        )
      )

  async def check_jaws(self) -> None:
    for entry in self.case["jaws"]:
      try:
        await self.commands.record_device_positions(
          spb.RecordDevicePositionsRequest(
            positions=[
              spb.DevicePosition(resource=self.ref(entry["gripper"]), jaw_width=entry["jawWidth"])
            ]
          )
        )
      except ServerError:
        if not entry.get("refused"):
          self.wrong.append(f"{entry['gripper']} open {entry['jawWidth']}: refused")
        continue
      if entry.get("refused"):
        self.wrong.append(f"{entry['gripper']} open {entry['jawWidth']}: expected a refusal")
        continue
      for part in entry["parts"]:
        at = (await self.named(part["resource"])).attributes.location
        if not vectors.close_vec((at.x, at.y, at.z), part["location"]):
          self.wrong.append(f"{part['resource']} at {at}, expected {part['location']}")

  async def check_placements(self) -> int:
    checked = 0
    for entry in self.case["placements"]:
      request = spb.BeginResourcePlaceRequest(
        frame=self.ref(self.case["frame"]),
        resource=self.ref(entry["resource"]),
        rotation_applied_by_move=entry["rotationAppliedByMove"],
        grip=spb.Grip(distance_from_bottom=0),
      )
      if "destination" in entry:
        request.destination_resource.CopyFrom(self.ref(entry["destination"]))
      else:
        request.destination_location.CopyFrom(vec(entry["destinationLocation"]))
      try:
        placed = await self.commands.begin_resource_place(request)
      except ServerError:
        continue  # refused by PLR too, or ruled out by a store rule
      at = placed.place_location
      self.expect(
        f"{entry['resource']} into {entry.get('destination', entry.get('destinationLocation'))}",
        entry,
        "location",
        (at.x, at.y, at.z),
        vectors.close_vec,
      )
      checked += 1
      await self.commands.resolve_operation(
        spb.ResolveOperationRequest(
          operation_id=placed.operation.id, outcomes=[spb.ItemOutcome(item=0, succeeded=False)]
        )
      )
    return checked

  async def check_grips(self) -> None:
    for entry in self.case["grips"]:
      grip = (
        spb.Grip(distance_from_top=entry["distanceFromTop"])
        if "distanceFromTop" in entry
        else spb.Grip(distance_from_bottom=entry["distanceFromBottom"])
      )
      try:
        picked = await self.commands.begin_resource_pickup(
          spb.BeginResourcePickupRequest(
            frame=self.ref(self.case["frame"]),
            resource=self.ref(entry["resource"]),
            carrier=self.ref("probe_arm"),
            grip=grip,
          )
        )
      except ServerError as refused:
        if not entry.get("refused"):
          self.wrong.append(f"{entry['resource']} gripped: refused ({refused})")
        continue
      self.expect(
        f"{entry['resource']} grip height", entry, "gripHeight", picked.grip_height, vectors.close
      )
      at = picked.grip_location
      if not entry.get("refused") and not vectors.close_vec(
        (at.x, at.y, at.z), entry["gripLocation"]
      ):
        self.wrong.append(f"{entry['resource']} grip at {at}, expected {entry['gripLocation']}")
      await self.commands.resolve_operation(
        spb.ResolveOperationRequest(
          operation_id=picked.operation.id, outcomes=[spb.ItemOutcome(item=0, succeeded=False)]
        )
      )

  async def run_case(self) -> None:
    await self.check_locations()
    await self.check_resources()
    await self.check_height_volume()
    await self.check_occupancy()
    await self.add_arm()
    await self.check_grips()
    placed = await self.check_placements()
    seated = await self.check_seats()
    await self.check_turns()
    await self.check_jaws()
    self.check()
    entries = len(self.case["placements"]) + len(self.case["seats"])
    if entries:
      self.assertGreater(placed + seated, entries // 4, "too many ruled out to check")


def _case(name: str) -> Dict[str, Any]:
  return next(case for case in VECTORS["cases"] if case["name"] == name)


class StarDeckVectorTests(ServerVectorTestCase):
  case = _case("star_deck")

  async def test_case(self):
    await self.run_case()


class TurnedDeckVectorTests(ServerVectorTestCase):
  case = _case("turned_deck")

  async def test_case(self):
    await self.run_case()


class TiltedVectorTests(ServerVectorTestCase):
  case = _case("tilted")

  async def test_case(self):
    await self.run_case()


if __name__ == "__main__":
  unittest.main()
