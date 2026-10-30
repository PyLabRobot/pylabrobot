"""The resource tree as a store holds it in memory: nodes linked to their parents and children,
each made from a part of a model.

A model is held once, as a tree of `Part`s. A part's facts (its kind, model, category, size and
its kind's spec) are shared by every resource made from it, and by every part alike in the model:
the wells of a plate given as a grid share one. A node keeps only what is its own: its name,
where it is (location, rotation), its identifier, barcode, metadata and preferred pickup location,
and its state. What a node is, is its part's facts; what a client reads is both, resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.models import compact_grids, identifier_of
from pylabrobot.resources.server.v1 import types_pb2 as pb

K = pb.ResourceKind

# The fields of `ResourceAttributes` that are a resource's own, not its model's.
INSTANCE_FIELDS = (
  "name",
  "location",
  "rotation",
  "identifier",
  "preferred_pickup_location",
  "barcode",
  "metadata",
  "display_model",
)

_CONTAINERS = (
  K.RESOURCE_KIND_CONTAINER,
  K.RESOURCE_KIND_WELL,
  K.RESOURCE_KIND_TROUGH,
  K.RESOURCE_KIND_TUBE,
  K.RESOURCE_KIND_PETRI_DISH,
  K.RESOURCE_KIND_TRASH,
)
_HOLDERS = (
  K.RESOURCE_KIND_RESOURCE,
  K.RESOURCE_KIND_RESOURCE_HOLDER,
  K.RESOURCE_KIND_PLATE_HOLDER,
  K.RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER,
  K.RESOURCE_KIND_PETRI_DISH_HOLDER,
)
# The kinds each spec belongs to: a resource of any other kind is refused one.
SPEC_KINDS: Dict[str, tuple] = {
  "container": _CONTAINERS,
  "itemized": (
    K.RESOURCE_KIND_PLATE,
    K.RESOURCE_KIND_TIP_RACK,
    K.RESOURCE_KIND_TUBE_RACK,
    K.RESOURCE_KIND_CONTAINER_RACK,
    K.RESOURCE_KIND_N_CHANNEL_PIPETTE,
  ),
  "plate": (K.RESOURCE_KIND_PLATE,),
  "tip_rack": (K.RESOURCE_KIND_TIP_RACK,),
  "head_tool": (K.RESOURCE_KIND_TIP, K.RESOURCE_KIND_HEAD_TOOL),
  "tip": (K.RESOURCE_KIND_TIP,),
  "tip_spot": (K.RESOURCE_KIND_TIP_SPOT,),
  "lid": (K.RESOURCE_KIND_LID,),
  "holder": _HOLDERS,
  "stack": (K.RESOURCE_KIND_RESOURCE_STACK,),
  "plate_adapter": (K.RESOURCE_KIND_PLATE_ADAPTER,),
  "pipette_channel": (K.RESOURCE_KIND_PIPETTE_CHANNEL,),
  "tip_mounting_shaft": (K.RESOURCE_KIND_TIP_MOUNTING_SHAFT,),
  "n_channel_pipette": (K.RESOURCE_KIND_N_CHANNEL_PIPETTE,),
  "link_body": (K.RESOURCE_KIND_LINK_BODY,),
  "mechanical_gripper": (K.RESOURCE_KIND_MECHANICAL_GRIPPER,),
  "deck": (K.RESOURCE_KIND_DECK,),
}


def to_coordinate(vector: pb.Vec3) -> Coordinate:
  """A protocol vector as a coordinate."""
  return Coordinate(vector.x, vector.y, vector.z)


def to_vec3(coordinate: Coordinate) -> pb.Vec3:
  """A coordinate as a protocol vector."""
  return pb.Vec3(x=coordinate.x, y=coordinate.y, z=coordinate.z)


def to_rotation(rotation: pb.Rotation) -> Rotation:
  """A protocol rotation as a rotation."""
  return Rotation(x=rotation.x, y=rotation.y, z=rotation.z)


def to_rotation_message(rotation: Rotation) -> pb.Rotation:
  """A rotation as a protocol rotation, by its Euler angles."""
  return pb.Rotation(x=rotation.x, y=rotation.y, z=rotation.z)


# -- parts ---------------------------------------------------------------------------------------


@dataclass(eq=False)
class Part:
  """One resource as a model makes it.

  Attributes:
    facts: what it is - its kind, model, category, size and its kind's spec - and none of the
      instance fields. Shared by every resource made from it, and by its model's parts alike. A
      tip spot's prototype tip named as the spot is kept without its name.
    start: the instance fields a resource made from it starts with; `{name}` in its name becomes
      the name of the resource made from the model's root.
    state: the state a resource made from it starts with, or None.
    children: the parts its children are made from, in order.
    parent: the part it is a child of, or None for the model's root.
    model: the name of the stored model it is part of; empty for an inline definition or a tip.
  """

  facts: pb.ResourceAttributes
  start: pb.ResourceAttributes
  state: Optional[pb.ResourceState] = None
  children: List[Part] = field(default_factory=list)
  parent: Optional[Part] = None
  model: str = ""
  _tip: Optional[Part] = None

  def walk(self) -> Iterator[Part]:
    """This part and every part under it, each before its children."""
    yield self
    for child in self.children:
      yield from child.walk()

  def tip(self) -> Part:
    """What a spot made from this part makes a tip from: its prototype tip."""
    if self._tip is None:
      prototype = pb.ResourceAttributes()
      prototype.CopyFrom(self.facts.tip_spot.prototype_tip)
      prototype.ClearField("location")
      facts, start = _split(prototype)
      self._tip = Part(facts=facts, start=start)
    return self._tip


def _split(attributes: pb.ResourceAttributes) -> tuple:
  """A resource's attributes as its model's facts and its own fields."""
  facts, own = pb.ResourceAttributes(), pb.ResourceAttributes()
  facts.CopyFrom(attributes)
  own.CopyFrom(attributes)
  for name in INSTANCE_FIELDS:
    facts.ClearField(name)
  for described, _ in attributes.ListFields():
    if described.name not in INSTANCE_FIELDS:
      own.ClearField(described.name)
  return facts, own


def _check_specs(attributes: pb.ResourceAttributes) -> None:
  """Refuse a resource with no kind, or a spec its kind does not take.

  Raises:
    ServerError: It has no kind, or a spec of another kind.
  """
  if attributes.kind == K.RESOURCE_KIND_UNSPECIFIED:
    raise ServerError(pb.PROBLEM_WRONG_KIND, f"'{attributes.name}' has no kind", attributes.name)
  for described, _ in attributes.ListFields():
    kinds = SPEC_KINDS.get(described.name)
    if kinds is not None and attributes.kind not in kinds:
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{attributes.name}', a {K.Name(attributes.kind)}, cannot have a {described.name} spec",
        attributes.name,
      )


class _Builder:
  """Parts for one definition; parts alike in it share one facts message."""

  def __init__(self, model: str):
    self.model = model
    self.facts: Dict[bytes, pb.ResourceAttributes] = {}

  def shared(self, attributes: pb.ResourceAttributes) -> tuple:
    facts, start = _split(attributes)
    if facts.HasField("itemized"):
      facts.itemized.ClearField("grid")
    if facts.HasField("tip_spot") and facts.tip_spot.prototype_tip.name == attributes.name:
      facts.tip_spot.prototype_tip.ClearField("name")
    key = facts.SerializeToString(deterministic=True)
    return self.facts.setdefault(key, facts), start

  def part(self, definition: pb.ResourceDefinition, parent: Optional[Part]) -> Part:
    attributes = definition.attributes
    _check_specs(attributes)
    facts, start = self.shared(attributes)
    part = Part(facts=facts, start=start, parent=parent, model=self.model)
    if definition.HasField("initial_state"):
      part.state = pb.ResourceState()
      part.state.CopyFrom(definition.initial_state)
    items: List[Part] = []
    if attributes.HasField("itemized") and attributes.itemized.HasField("grid"):
      items = self.grid(definition, part)
    part.children = items + [self.part(child, part) for child in definition.children]
    for child in part.children:
      if not child.start.name:
        raise ServerError(pb.PROBLEM_NOT_FOUND, "a resource needs a name")
    return part

  def grid(self, definition: pb.ResourceDefinition, parent: Part) -> List[Part]:
    """The items of a grid, column-major, alike but for where they are and what they are called.

    Raises:
      ServerError: A grid in a resource that also lists items, or one with no items.
    """
    attributes = definition.attributes
    spec = attributes.itemized
    if any(child.attributes.identifier for child in definition.children):
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{attributes.name}' gives its items both as a grid and as children",
        attributes.name,
      )
    if spec.num_items_x < 1 or spec.num_items_y < 1:
      raise ServerError(
        pb.PROBLEM_OUT_OF_RANGE,
        f"'{attributes.name}' has a grid of {spec.num_items_x} by {spec.num_items_y} items",
        attributes.name,
      )
    grid = spec.grid
    template = self.part(grid.item, None)
    items = []
    for column in range(spec.num_items_x):
      for row in range(spec.num_items_y):
        identifier = identifier_of(column, row)
        item = _named_copy(template, parent, "{identifier}", identifier)
        item.start.identifier = identifier
        item.start.location.CopyFrom(
          pb.Vec3(
            x=grid.offset.x + column * grid.item_dx,
            y=grid.offset.y + (spec.num_items_y - row - 1) * grid.item_dy,
            z=grid.offset.z,
          )
        )
        items.append(item)
    return items


def _named_copy(part: Part, parent: Optional[Part], old: str, new: str) -> Part:
  """A copy of a part and the parts under it, sharing their facts, with `old` in their names
  made `new`."""
  start = pb.ResourceAttributes()
  start.CopyFrom(part.start)
  start.name = start.name.replace(old, new)
  facts = part.facts
  if old in facts.tip_spot.prototype_tip.name:
    facts = pb.ResourceAttributes()
    facts.CopyFrom(part.facts)
    prototype = facts.tip_spot.prototype_tip
    prototype.name = prototype.name.replace(old, new)
  copy = Part(facts=facts, start=start, state=part.state, parent=parent, model=part.model)
  copy.children = [_named_copy(child, copy, old, new) for child in part.children]
  return copy


def build_parts(definition: pb.ResourceDefinition, model: str = "") -> Part:
  """The parts of a definition, its grids' items made.

  Args:
    definition: What to make; it is not changed.
    model: The name of the stored model the parts are of; empty for an inline definition.

  Raises:
    ServerError: A resource with no kind, a spec of another kind, a child with no name, or a grid
      refused.
  """
  return _Builder(model).part(definition, None)


# -- nodes ---------------------------------------------------------------------------------------


@dataclass(eq=False)
class Node:
  """One resource in a tree: the part it is made from, what is its own, its state, and where it
  hangs.

  Compared by identity: two nodes are the same only if they are the same object.

  Attributes:
    part: what it is made from; its facts are what it is.
    own: its own fields: its name, where it is relative to its parent, its identifier, barcode,
      metadata and preferred pickup location.
    state: its volume, tracking flags, jaw width and tip count.
    parent: what it hangs from, or None for a root.
    children: what hangs from it, in order.
    id: the store's id for it; empty until a store gives it one.
  """

  part: Part
  own: pb.ResourceAttributes = field(default_factory=pb.ResourceAttributes)
  state: pb.ResourceState = field(default_factory=pb.ResourceState)
  parent: Optional[Node] = None
  children: List[Node] = field(default_factory=list)
  id: bytes = b""

  @property
  def facts(self) -> pb.ResourceAttributes:
    """What it is, as its model has it: its kind, model, category, size and its kind's spec."""
    return self.part.facts

  @property
  def name(self) -> str:
    return self.own.name

  @property
  def kind(self) -> pb.ResourceKind:
    return self.part.facts.kind

  @property
  def location(self) -> Optional[Coordinate]:
    """Where it is relative to its parent, or None where that is unknown."""
    if not self.own.HasField("location"):
      return None
    return to_coordinate(self.own.location)

  @property
  def rotation(self) -> Rotation:
    return to_rotation(self.own.rotation)

  def walk(self) -> Iterator[Node]:
    """This node and everything under it, depth first, each before its children."""
    yield self
    for child in self.children:
      yield from child.walk()

  def all_children(self) -> List[Node]:
    """Everything under this node, in `Resource.get_all_children`'s order: its children first,
    then what is under each of them in turn."""
    found = list(self.children)
    for child in self.children:
      found += child.all_children()
    return found

  def root(self) -> Node:
    node = self
    while node.parent is not None:
      node = node.parent
    return node

  def find(self, name: str) -> Optional[Node]:
    """The node called `name` in this subtree, or None."""
    return next((node for node in self.walk() if node.name == name), None)


def resolved(node: Node) -> pb.ResourceAttributes:
  """A node's attributes as a client reads them: its part's facts and its own fields."""
  attributes = pb.ResourceAttributes()
  attributes.CopyFrom(node.part.facts)
  attributes.MergeFrom(node.own)
  if attributes.HasField("tip_spot") and attributes.tip_spot.HasField("prototype_tip"):
    prototype = attributes.tip_spot.prototype_tip
    if not prototype.name:
      prototype.name = node.own.name
  return attributes


def instantiate(part: Part, name: Optional[str] = None, parent: Optional[Node] = None) -> Node:
  """Nodes for a part and every part under it, hung from `parent` when given.

  Args:
    part: What to make them from.
    name: The root's name, put where the names under it say `{name}`; unset keeps the part's.
    parent: What to hang the root from.
  """
  root_name = part.start.name if name is None else name

  def make(each: Part, above: Optional[Node], is_root: bool) -> Node:
    own = pb.ResourceAttributes()
    own.CopyFrom(each.start)
    own.name = root_name if is_root else own.name.replace("{name}", root_name)
    state = pb.ResourceState()
    if each.state is not None:
      state.CopyFrom(each.state)
    node = Node(part=each, own=own, state=state, parent=above)
    if above is not None:
      above.children.append(node)
    for child in each.children:
      make(child, node, False)
    return node

  return make(part, parent, True)


def build_tree(definition: pb.ResourceDefinition, parent: Optional[Node] = None) -> Node:
  """Nodes for a definition and everything in it, hung from `parent` when given.

  Raises:
    ServerError: The definition is refused; see `build_parts`.
  """
  return instantiate(build_parts(definition), parent=parent)


def to_definition(node: Node, compact: bool = False) -> pb.ResourceDefinition:
  """The definition of a node and everything under it, as it is now.

  Args:
    node: What to define.
    compact: Give uniform grids of items once, as an `ItemGrid`, as models do.
  """
  definition = pb.ResourceDefinition()
  definition.attributes.CopyFrom(resolved(node))
  if node.state.ByteSize() > 0:
    definition.initial_state.CopyFrom(node.state)
  definition.children.extend(to_definition(child) for child in node.children)
  if compact:
    compact_grids(definition)
  return definition


def copy_tree(node: Node) -> Node:
  """A copy of a node and everything under it, belonging to no tree."""

  def copy(each: Node, parent: Optional[Node]) -> Node:
    own, state = pb.ResourceAttributes(), pb.ResourceState()
    own.CopyFrom(each.own)
    state.CopyFrom(each.state)
    made = Node(part=each.part, own=own, state=state, parent=parent, id=each.id)
    made.children = [copy(child, made) for child in each.children]
    return made

  return copy(node, None)
