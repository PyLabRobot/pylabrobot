"""The device commands a store stages: tools, liquid, resources picked up and placed, and the
primitives' changes.

Each command checks what it is asked against the model first, answers what the driver needs to plan
the command, and stages what the device will do as an operation, one item per channel or per
resource, for `ResolveOperation` to commit or roll back once the device has done it.

What the model disagrees with is an issue. Asked to record, the command goes ahead and the model
follows what the device does; asked to reject, it refuses and changes nothing.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.server import geometry as g
from pylabrobot.resources.server.engine import (
  Engine,
  Item,
  Step,
  Transfer,
  capacity,
  check_stack_top,
  tool_in,
  volume_issue,
)
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tree import Node, to_coordinate, to_rotation_message, to_vec3
from pylabrobot.resources.server.v1 import primitives_pb2 as ppb
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb

K = pb.ResourceKind

_EMPTY = 1e-6


class _Issues:
  """The disagreements a command finds before it changes anything."""

  def __init__(self, policy: pb.OnTrackingProblem):
    self.policy = policy
    self.found: List[pb.Issue] = []

  def add(self, problem: pb.Problem, message: str, node: Optional[Node], item: int) -> None:
    self.found.append(
      pb.Issue(
        problem=problem,
        message=message,
        resource_id=node.id if node is not None else b"",
        item=item,
      )
    )

  def check(self) -> None:
    """Refuse the command when asked to and there is anything to refuse.

    Raises:
      ServerError: The policy is to reject, and there is an issue.
    """
    if self.policy == pb.ON_TRACKING_PROBLEM_REJECT and self.found:
      first = self.found[0]
      raise ServerError(first.problem, first.message)


def _tracks_volume(node: Node) -> bool:
  return node.state.HasField("volume") and node.state.volume.tracking_enabled


def _tracks_tips(spot: Node) -> bool:
  return not spot.state.HasField("tip_tracking_enabled") or spot.state.tip_tracking_enabled


def _shaft(engine: Engine, ref: pb.ResourceRef) -> Node:
  shaft = engine.resolve(ref)
  if shaft.kind != K.RESOURCE_KIND_TIP_MOUNTING_SHAFT:
    raise ServerError(pb.PROBLEM_WRONG_KIND, f"'{shaft.name}' is not a shaft", shaft.name)
  return shaft


def _context(engine: Engine, refs: Sequence[pb.ResourceRef]) -> List[spb.ShaftContext]:
  contexts = []
  for ref in refs:
    shaft = _shaft(engine, ref)
    tool = tool_in(shaft)
    context = spb.ShaftContext(shaft_id=shaft.id)
    if tool is not None:
      context.tool.CopyFrom(engine.node_message(tool))
    contexts.append(context)
  return contexts


def _lidded(node: Node) -> Optional[Node]:
  """The resource with a lid on that covers this one: itself or a plate it is in."""
  current: Optional[Node] = node
  while current is not None:
    if g.lid_of(current) is not None:
      return current
    current = current.parent
  return None


def _origin(engine: Engine, tool: Node) -> Optional[Node]:
  """Where a tool came from: the spot a tip is named for, or what a tool was taken from."""
  if tool.kind == K.RESOURCE_KIND_TIP and "#" in tool.name:
    spot_name = tool.name.rsplit("#", 1)[0]
    found = engine.named(tool.root(), spot_name)
    if found is not None:
      return found
  source = engine.origins.get(tool.id)
  return engine.nodes.get(source) if source is not None else None


# -- tools ---------------------------------------------------------------------------------------


def begin_tool_pickup(
  engine: Engine, request: spb.BeginToolPickupRequest
) -> spb.BeginToolPickupResponse:
  """Stage each shaft collecting a tool: a spot's tip, a fresh one from an untracked spot, a
  parked tool, or a tool named itself where it is parked."""
  frame = engine.resolve(request.frame)
  issues = _Issues(request.on_tracking_problem)
  plans: List[Tuple[Node, Node, Optional[Node]]] = []  # source, shaft, tool already there
  for index, item in enumerate(request.items):
    source, shaft = engine.resolve(item.source), _shaft(engine, item.shaft)
    held = tool_in(shaft)
    if held is not None:
      issues.add(pb.PROBLEM_HAS_TIP, f"'{shaft.name}' already carries '{held.name}'", shaft, index)
    tool = source if source.kind in g.TOOL_KINDS else tool_in(source)
    if source.kind == K.RESOURCE_KIND_TIP_SPOT:
      if _tracks_tips(source) and tool is None:
        issues.add(pb.PROBLEM_NO_TIP, f"'{source.name}' holds no tip", source, index)
      if not _tracks_tips(source):
        tool = None
    elif tool is None:
      issues.add(pb.PROBLEM_NO_TIP, f"'{source.name}' holds no tool", source, index)
    plans.append((source, shaft, tool))
  rack_lids = {
    id(lidded): lidded
    for source, _, _ in plans
    for lidded in [_lidded(source)]
    if lidded is not None
  }
  for lidded in rack_lids.values():
    issues.add(pb.PROBLEM_LID_ON, f"'{lidded.name}' has its lid on", lidded, -1)
  issues.check()
  # The frame is only read: what moves is a tool, from its source onto its shaft.
  changed = [n for s, sh, _ in plans for n in (s, sh)]
  engine.check_locks(changed, request.lock_tokens)

  placements: g.Placements = {}
  answers = [engine.placement(source, frame, placements) for source, _, _ in plans]
  items: List[Item] = []
  response = spb.BeginToolPickupResponse(issues=issues.found)
  for index, ((source, shaft, tool), placement) in enumerate(zip(plans, answers)):
    staged = Item()
    if tool is None:
      if source.kind != K.RESOURCE_KIND_TIP_SPOT:
        raise ServerError(pb.PROBLEM_NO_TIP, f"'{source.name}' holds no tool", source.name)
      tool = engine.make_tip(source, staged.steps)
    elif tool is source:
      # Named itself: it goes back to what it is parked in, where it stands there.
      if tool.parent is not None:
        engine.origins[tool.id] = tool.parent.id
      if tool.location is not None:
        engine.origin_locations[tool.id] = tool.location
    else:
      engine.origins[tool.id] = source.id
    held = tool_in(shaft)
    if held is not None:
      engine.remove(held, staged.steps)
    engine.move(tool, shaft, None, g.mount_location(shaft, tool), staged.steps)
    items.append(staged)
    response.items.append(
      spb.ToolPickupPlan(item=index, tool=engine.node_message(tool), source=placement)
    )
  op = engine.stage(
    frame.root(), "BeginToolPickup", engine.holder_of(request.lock_tokens), items, changed
  )
  response.operation.CopyFrom(op.message())
  response.context_shafts.extend(_context(engine, request.context_shafts))
  return response


def begin_tool_drop(engine: Engine, request: spb.BeginToolDropRequest) -> spb.BeginToolDropResponse:
  """Stage each shaft letting go of its tool: into a spot or holder, back where it came from, or
  into a trash, which keeps it until cleared. A tip let go into an untracked spot leaves the
  store."""
  frame = engine.resolve(request.frame)
  issues = _Issues(request.on_tracking_problem)
  plans: List[Tuple[Node, Optional[Node], Node]] = []  # shaft, tool, destination
  for index, item in enumerate(request.items):
    shaft = _shaft(engine, item.shaft)
    tool = tool_in(shaft)
    if tool is None:
      issues.add(pb.PROBLEM_NO_TIP, f"'{shaft.name}' carries no tool", shaft, index)
    elif tool.kind == K.RESOURCE_KIND_TIP and _tracks_volume(tool):
      if tool.state.volume.pending_volume > _EMPTY:
        issues.add(
          pb.PROBLEM_TIP_NOT_EMPTY,
          f"'{tool.name}' still holds {tool.state.volume.pending_volume} uL",
          tool,
          index,
        )
    if item.WhichOneof("destination") == "origin":
      if tool is None:
        raise ServerError(pb.PROBLEM_NO_TIP, f"'{shaft.name}' carries no tool", shaft.name)
      destination = _origin(engine, tool)
      if destination is None:
        raise ServerError(
          pb.PROBLEM_NOT_FOUND, f"where '{tool.name}' came from is not known", tool.name
        )
    else:
      destination = engine.resolve(item.target)
    if destination.kind == K.RESOURCE_KIND_TRASH and tool is not None:
      engine.check_names_fit(tool, destination)
    if destination.kind == K.RESOURCE_KIND_TIP_SPOT and _tracks_tips(destination):
      held = tool_in(destination)
      if held is not None and held is not tool:
        issues.add(
          pb.PROBLEM_HAS_TIP,
          f"'{destination.name}' already holds '{held.name}'",
          destination,
          index,
        )
    plans.append((shaft, tool, destination))
  issues.check()
  changed = [n for s, _, d in plans for n in (s, d)]
  engine.check_locks(changed, request.lock_tokens)

  placements: g.Placements = {}
  answers = [engine.placement(destination, frame, placements) for _, _, destination in plans]
  items: List[Item] = []
  response = spb.BeginToolDropResponse(issues=issues.found)
  for index, ((shaft, tool, destination), placement) in enumerate(zip(plans, answers)):
    staged = Item()
    plan = spb.ToolDropPlan(item=index, destination=placement)
    if tool is not None:
      plan.tool.CopyFrom(engine.node_message(tool))
      # An untracked spot is left as it is: the tip dropped into it is gone.
      kept = destination.kind != K.RESOURCE_KIND_TIP_SPOT or _tracks_tips(destination)
      to_origin = request.items[index].WhichOneof("destination") == "origin"
      # A tool taken by name goes back where it stood, beside whatever else is parked there.
      parked_at = engine.origin_locations.get(tool.id) if to_origin else None
      if destination.kind == K.RESOURCE_KIND_TRASH:
        engine.move(tool, destination, None, g.trash_location(destination, tool), staged.steps)
      elif kept and parked_at is not None and destination.kind != K.RESOURCE_KIND_TIP_SPOT:
        engine.move(tool, destination, None, parked_at, staged.steps)
      elif kept:
        held = tool_in(destination)
        if held is not None and held is not tool:
          engine.remove(held, staged.steps)
        location = (
          g.resting_location(destination, tool)
          if destination.kind == K.RESOURCE_KIND_TIP_SPOT
          else g.default_child_location(destination, tool)
        )
        if location is None:
          raise ServerError(
            pb.PROBLEM_NO_LOCATION, f"'{destination.name}' does not seat tools", destination.name
          )
        engine.move(tool, destination, None, location, staged.steps)
      else:
        engine.remove(tool, staged.steps)
    items.append(staged)
    response.items.append(plan)
  op = engine.stage(
    frame.root(), "BeginToolDrop", engine.holder_of(request.lock_tokens), items, changed
  )
  response.operation.CopyFrom(op.message())
  response.context_shafts.extend(_context(engine, request.context_shafts))
  return response


# -- liquid --------------------------------------------------------------------------------------


def begin_liquid_transfer(
  engine: Engine, request: spb.BeginLiquidTransferRequest
) -> spb.BeginLiquidTransferResponse:
  """Stage liquid moving between each container and the tip on its shaft."""
  frame = engine.resolve(request.frame)
  issues = _Issues(request.on_tracking_problem)
  aspirate = request.direction == spb.TRANSFER_DIRECTION_ASPIRATE
  if request.direction == spb.TRANSFER_DIRECTION_UNSPECIFIED:
    raise ServerError(pb.PROBLEM_UNSPECIFIED, "a transfer needs a direction")
  plans: List[Tuple[Node, Node, float]] = []
  # What each resource holds once the items before it book: two channels may share a trough.
  pending: Dict[int, float] = {}

  def held(node: Node) -> float:
    return pending.get(id(node), node.state.volume.pending_volume)

  for index, item in enumerate(request.items):
    container, shaft = engine.resolve(item.container), _shaft(engine, item.shaft)
    tip = tool_in(shaft)
    if container.kind not in g.CONTAINER_KINDS:
      raise ServerError(
        pb.PROBLEM_WRONG_KIND, f"'{container.name}' holds no liquid", container.name
      )
    if tip is None or tip.kind != K.RESOURCE_KIND_TIP:
      raise ServerError(pb.PROBLEM_NO_TIP, f"'{shaft.name}' carries no tip", shaft.name)
    lidded = _lidded(container)
    if lidded is not None:
      issues.add(pb.PROBLEM_LID_ON, f"'{lidded.name}' has its lid on", lidded, index)
    giver, taker = (container, tip) if aspirate else (tip, container)
    if _tracks_volume(giver) and held(giver) + _EMPTY < item.volume:
      issues.add(
        pb.PROBLEM_TOO_LITTLE_LIQUID,
        f"'{giver.name}' holds {held(giver)} uL, not {item.volume}",
        giver,
        index,
      )
    capacity = (
      taker.facts.tip.maximal_volume
      if taker.kind == K.RESOURCE_KIND_TIP
      else taker.facts.container.max_volume
    )
    if _tracks_volume(taker) and held(taker) + item.volume > capacity + _EMPTY:
      issues.add(
        pb.PROBLEM_TOO_LITTLE_VOLUME,
        f"'{taker.name}' has room for {capacity - held(taker)} uL, not {item.volume}",
        taker,
        index,
      )
    if _tracks_volume(giver):
      pending[id(giver)] = held(giver) - item.volume
    if _tracks_volume(taker):
      pending[id(taker)] = held(taker) + item.volume
    plans.append((container, tip, item.volume))
  issues.check()
  changed = [c for c, _, _ in plans]
  engine.check_locks(changed, request.lock_tokens)

  placements: g.Placements = {}
  answers = [engine.placement(container, frame, placements) for container, _, _ in plans]
  items: List[Item] = []
  response = spb.BeginLiquidTransferResponse(issues=issues.found)
  for index, ((container, tip, volume), placement) in enumerate(zip(plans, answers)):
    staged = Item()
    giver, taker = (container, tip) if aspirate else (tip, container)
    if _tracks_volume(giver):
      engine.book(giver, -volume, staged.steps)
    if _tracks_volume(taker):
      engine.book(taker, volume, staged.steps)
    staged.transfers.append(
      Transfer(
        giver=giver if _tracks_volume(giver) else None,
        taker=taker if _tracks_volume(taker) else None,
        volume=volume,
      )
    )
    items.append(staged)
    response.items.append(
      spb.TransferPlan(item=index, container=placement, tip=engine.node_message(tip))
    )
  op = engine.stage(
    frame.root(), "BeginLiquidTransfer", engine.holder_of(request.lock_tokens), items, changed
  )
  response.operation.CopyFrom(op.message())
  return response


# -- resources picked up and placed --------------------------------------------------------------


def _grip(resource: Node, grip: spb.Grip) -> float:
  which = grip.WhichOneof("height")
  return g.grip_height(
    resource,
    grip.distance_from_top if which == "distance_from_top" else None,
    grip.distance_from_bottom if which == "distance_from_bottom" else None,
  )


def _keep_turned(resource: Node, new_parent: Node) -> Rotation:
  """The rotation that keeps a resource turned as it is, along its new parent's axes."""
  return g.rotation_wrt(resource, new_parent)


def begin_resource_pickup(
  engine: Engine, request: spb.BeginResourcePickupRequest
) -> spb.BeginResourcePickupResponse:
  """Stage a resource moving onto what carries it, where it is held, turned as it is."""
  frame = engine.resolve(request.frame)
  resource, carrier = engine.resolve(request.resource), engine.resolve(request.carrier)
  engine.check_locks([carrier], request.lock_tokens, moved=[resource])
  check_stack_top(resource, "be picked up")
  if g.is_in_subtree_of(carrier, resource):
    raise ServerError(
      pb.PROBLEM_CANNOT_DROP_HERE, f"'{carrier.name}' is in '{resource.name}'", resource.name
    )
  engine.check_names_fit(resource, carrier)
  placements: g.Placements = {}
  pick = g.location_wrt(resource, frame, placements=placements)
  height = _grip(resource, request.grip)
  grip = g.grip_location(resource, pick, frame, height)
  size = g.absolute_size(resource)
  rotation_in_frame = g.rotation_wrt(resource, frame)
  response = spb.BeginResourcePickupResponse(
    resource=engine.node_message(resource),
    pick_location=to_vec3(pick),
    grip_location=to_vec3(grip),
    grip_height=height,
    absolute_size=pb.Vec3(x=size.x, y=size.y, z=size.z),
    rotation_in_frame=to_rotation_message(rotation_in_frame),
  )
  staged = Item()
  turned = _keep_turned(resource, carrier)
  held_at = g.placed_child_location(carrier, frame, pick)
  engine.move(resource, carrier, None, held_at, staged.steps)
  engine.set_attributes(
    resource,
    staged.steps,
    lambda: resource.own.rotation.CopyFrom(to_rotation_message(turned)),
  )
  op = engine.stage(
    frame.root(),
    "BeginResourcePickup",
    engine.holder_of(request.lock_tokens),
    [staged],
    changed=[carrier],
    moved=[resource],
  )
  response.operation.CopyFrom(op.message())
  return response


def _carrying_branch(resource: Node, frame: Node) -> Optional[Node]:
  """What carries a held resource, as it stands in the frame: the frame's child it hangs under,
  when something between them is a tool, a shaft or a gripper."""
  carriers = {
    K.RESOURCE_KIND_HEAD_TOOL,
    K.RESOURCE_KIND_TIP_MOUNTING_SHAFT,
    K.RESOURCE_KIND_MECHANICAL_GRIPPER,
  }
  held = False
  current = resource.parent
  branch: Optional[Node] = None
  while current is not None and current is not frame:
    held = held or current.kind in carriers
    branch = current
    current = current.parent
  return branch if held and current is frame else None


def begin_resource_place(
  engine: Engine, request: spb.BeginResourcePlaceRequest
) -> spb.BeginResourcePlaceResponse:
  """Stage a resource let go into its destination, turned as the move turns it.

  A location puts it on the frame there, and is refused when it falls inside something. A trash
  keeps what is let go into it, where it was let go, until it is cleared.
  """
  frame = engine.resolve(request.frame)
  resource = engine.resolve(request.resource)
  placements: g.Placements = {}
  destination: Optional[Node] = None
  if request.WhichOneof("destination") == "destination_location":
    location = to_coordinate(request.destination_location)
    branch = _carrying_branch(resource, frame)
    exclude = [resource] + ([branch] if branch is not None else [])
    occupant = g.resource_at(frame, location, exclude, placements)
    if occupant is not None:
      raise ServerError(
        pb.PROBLEM_CANNOT_DROP_HERE,
        f"{location} is inside '{occupant.name}': give that, or what holds it, as the destination",
        resource.name,
      )
    target: Node = frame
    place = location
    rotation = g.rotation_in_destination(
      resource, location, frame, request.rotation_applied_by_move
    )
  else:
    destination = engine.resolve(request.destination_resource)
    engine.check_drop(resource, destination)
    target = destination
    place = g.place_location(resource, destination, frame, request.rotation_applied_by_move)
    rotation = g.rotation_in_destination(
      resource, destination, frame, request.rotation_applied_by_move
    )
  engine.check_locks([target], request.lock_tokens, moved=[resource])
  engine.check_names_fit(resource, target)
  height = _grip(resource, request.grip)
  grip = g.grip_location(resource, place, frame, height)
  response = spb.BeginResourcePlaceResponse(
    place_location=to_vec3(place),
    grip_location=to_vec3(grip),
    grip_height=height,
    rotation_in_destination=to_rotation_message(Rotation(z=rotation)),
  )
  if destination is not None:
    response.destination.CopyFrom(engine.node_message(destination))

  staged = Item()
  turn = pb.RotationChange(resource=pb.ResourceRef(id=resource.id))
  turn.by.z = rotation - resource.rotation.z
  engine.rotate(turn, staged.steps)
  if target.kind == K.RESOURCE_KIND_RESOURCE_STACK:
    seated = g.stack_child_location(target, resource)
    engine.move(resource, target, None, seated, staged.steps)
    engine.reseat_stack(target, staged.steps)
  elif target is frame and destination is None:
    engine.move(resource, frame, None, place, staged.steps)
  else:
    child = g.placed_child_location(target, frame, place)
    engine.move(resource, target, None, child, staged.steps)
  op = engine.stage(
    frame.root(),
    "BeginResourcePlace",
    engine.holder_of(request.lock_tokens),
    [staged],
    changed=[target],
    moved=[resource],
  )
  response.operation.CopyFrom(op.message())
  return response


# -- the primitives ------------------------------------------------------------------------------


def _change_nodes(engine: Engine, change: ppb.Change) -> List[Node]:
  """The resources a change touches, for the lock check."""
  which = change.WhichOneof("change")
  if which == "assign_child":
    return [engine.resolve(change.assign_child.child), engine.resolve(change.assign_child.parent)]
  if which == "unassign_child":
    return [engine.resolve(change.unassign_child.resource)]
  if which == "set_location":
    return [engine.resolve(change.set_location.resource)]
  if which == "rotation":
    return [engine.resolve(change.rotation.resource)]
  if which == "set_volume":
    return [engine.resolve(change.set_volume.resource)]
  if which == "add_liquid":
    return [engine.resolve(change.add_liquid.resource)]
  if which == "remove_liquid":
    return [engine.resolve(change.remove_liquid.resource)]
  if which == "set_tracking":
    return [engine.resolve(change.set_tracking.scope)]
  if which == "set_tip_presence":
    return [engine.resolve(ref) for ref in change.set_tip_presence.spots]
  if which == "set_metadata":
    return [engine.resolve(change.set_metadata.resource)]
  if which == "set_barcode":
    return [engine.resolve(change.set_barcode.resource)]
  if which == "set_jaw_width":
    return [engine.resolve(change.set_jaw_width.gripper)]
  raise ServerError(pb.PROBLEM_UNSPECIFIED, "an empty change")


def _apply_change(
  engine: Engine,
  change: ppb.Change,
  tokens: Sequence[bytes],
  steps: List[Step],
  staged: bool,
  issues: _Issues,
  index: int,
) -> List[Node]:
  which = change.WhichOneof("change")
  if which == "assign_child":
    return [engine.assign(change.assign_child, tokens, steps)]
  if which == "unassign_child":
    node = engine.resolve(change.unassign_child.resource)
    engine.unassign(node, tokens, steps)
    return [node]
  if which == "set_location":
    node = engine.resolve(change.set_location.resource)
    partial, here = change.set_location.location, node.location or Coordinate(0, 0, 0)
    location = Coordinate(
      partial.x if partial.HasField("x") else here.x,
      partial.y if partial.HasField("y") else here.y,
      partial.z if partial.HasField("z") else here.z,
    )
    engine.set_attributes(node, steps, lambda: node.own.location.CopyFrom(to_vec3(location)))
    return [node]
  if which == "rotation":
    return [engine.rotate(change.rotation, steps)]
  if which == "set_volume":
    node = engine.resolve(change.set_volume.resource)
    issue = volume_issue(node, change.set_volume.volume, index)
    if issue is not None:
      issues.add(issue.problem, issue.message, node, index)
    engine.set_volume(node, change.set_volume.volume, steps)
    return [node]
  if which in ("add_liquid", "remove_liquid"):
    message = change.add_liquid if which == "add_liquid" else change.remove_liquid
    node = engine.resolve(message.resource)
    if node.kind not in g.CONTAINER_KINDS and node.kind != K.RESOURCE_KIND_TIP:
      raise ServerError(pb.PROBLEM_WRONG_KIND, f"'{node.name}' holds no liquid", node.name)
    delta = message.volume if which == "add_liquid" else -message.volume
    if node.state.volume.pending_volume + delta < -_EMPTY:
      issues.add(
        pb.PROBLEM_TOO_LITTLE_LIQUID,
        f"'{node.name}' holds {node.state.volume.pending_volume} uL",
        node,
        index,
      )
    most = capacity(node)
    if most is not None and node.state.volume.pending_volume + delta > most + _EMPTY:
      issues.add(
        pb.PROBLEM_TOO_LITTLE_VOLUME,
        f"'{node.name}' has room for {most - node.state.volume.pending_volume} uL, not {delta}",
        node,
        index,
      )
    engine.book(node, delta, steps)
    if not staged:
      engine.set_attributes(node, steps, lambda: _commit(node, delta))
    return [node]
  if which == "set_tracking":
    engine.set_tracking(change.set_tracking, steps)
    return list(engine.resolve(change.set_tracking.scope).walk())
  if which == "set_tip_presence":
    engine.set_tip_presence(change.set_tip_presence, steps)
    return [engine.resolve(ref) for ref in change.set_tip_presence.spots]
  if which == "set_metadata":
    engine.set_metadata(change.set_metadata, steps)
    return [engine.resolve(change.set_metadata.resource)]
  if which == "set_barcode":
    engine.set_barcode(change.set_barcode, steps)
    return [engine.resolve(change.set_barcode.resource)]
  if which == "set_jaw_width":
    gripper = engine.resolve(change.set_jaw_width.gripper)
    engine.set_jaw_width(gripper, change.set_jaw_width.jaw_width, steps)
    return [gripper]
  raise ServerError(pb.PROBLEM_UNSPECIFIED, "an empty change")


def _commit(node: Node, delta: float) -> None:
  node.state.volume.volume += delta


def apply_changes(engine: Engine, request: ppb.ApplyChangesRequest) -> ppb.ApplyChangesResponse:
  """Low-level changes in order, all or none; staged as an operation, one item per group."""
  touched = [n for group in request.groups for c in group.changes for n in _change_nodes(engine, c)]
  engine.check_locks(touched, request.lock_tokens)
  issues = _Issues(request.on_tracking_problem)
  items: List[Item] = []
  changed: List[Node] = []
  for index, group in enumerate(request.groups):
    staged = Item()
    for change in group.changes:
      changed += _apply_change(
        engine, change, request.lock_tokens, staged.steps, request.staged, issues, index
      )
    items.append(staged)
  issues.check()
  response = ppb.ApplyChangesResponse(issues=issues.found)
  if request.staged and items:
    root = touched[0].root() if touched else None
    if root is not None:
      op = engine.stage(root, "ApplyChanges", engine.holder_of(request.lock_tokens), items, touched)
      response.operation.CopyFrom(op.message())
  seen = set()
  for node in changed:
    if node.id in engine.nodes and node.id not in seen:
      seen.add(node.id)
      response.nodes.append(engine.node_message(node))
  return response
