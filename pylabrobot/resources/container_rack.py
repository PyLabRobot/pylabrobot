"""A movable rack of holders parameterized by container content type."""

from typing import ClassVar, Dict, Generic, List, Optional, Sequence, TypeVar, Union, cast

from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder

T = TypeVar("T", bound=Resource)


class ContainerRack(ItemizedResource[ResourceHolder], Generic[T]):
  """A movable rack of holders parameterized by content type ``T``.

  Slots are :class:`ResourceHolder` cells in a 2D grid; each slot may be empty
  or hold a resource of type ``T``. Dimensions and item layout are caller-defined,
  so the rack is not restricted to any particular footprint (the SLAS-1
  127.76 x 85.48 mm footprint is one common case — see e.g.
  :func:`pylabrobot.resources.alpaqua.tube_racks.alpaqua_12_tuberack_5mL_eppis`).

  Subclasses pick a concrete content type by parameterizing ``T`` and overriding
  ``_content_type`` (used for runtime ``isinstance`` enforcement, since Python
  erases generic parameters at runtime). The base class uses :class:`Resource`,
  i.e. accepts anything.

  Identifiers in ``ordered_items`` may be any string — Excel notation
  (``"A1"``, ``"B2"``), descriptive names (``"primary"``, ``"left_well"``),
  or anything else. Slice syntax (``rack["A1:A3"]``) and row/column helpers
  inherited from :class:`ItemizedResource` only work for Excel-style keys;
  single-key access (``rack["primary"]``) works for any identifier. This
  matters for irregularly shaped racks that don't fit a Cartesian grid.

  Examples:
      >>> # Heterogeneous rack with Excel-style identifiers:
      >>> rack: ContainerRack[Resource] = ContainerRack(
      ...     name="mixed", size_x=127.76, size_y=85.48, size_z=45.0,
      ...     ordered_items=holders,
      ... )
      >>> rack["A1"] = my_tube
      >>> rack["A1:A3"] = [tube1, tube2, tube3]
      >>>
      >>> # Irregular rack with custom identifiers:
      >>> rack["primary_well"] = my_tube
      >>> rack["overflow"] = backup_tube
      >>>
      >>> # Typed rack (only Tubes), via subclass:
      >>> tr: TubeRack = TubeRack(...)
      >>> tube: Tube = tr.get_container("A1")  # statically typed Tube
  """

  _content_type: ClassVar[type] = Resource

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, ResourceHolder]] = None,
    model: Optional[str] = None,
    category: str = "container_rack",
  ):
    if ordered_items is None or len(ordered_items) == 0:
      raise ValueError("ordered_items must be provided and non-empty")

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      ordered_items=ordered_items,
      category=category,
      model=model,
    )

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, "
      f"size_x={self._size_x}, size_y={self._size_y}, size_z={self._size_z}, "
      f"location={self.location})"
    )

  # =========================================================================
  # CONTAINER ACCESS
  # =========================================================================

  def get_container(self, identifier: Union[str, int]) -> T:
    """Get the container at a position. Raises if the slot is empty."""
    holder = self.get_item(identifier)
    if holder.resource is None:
      raise ValueError(
        f"No container at position {identifier} in rack '{self.name}'. "
        f"Use has_container() to check first."
      )
    return cast(T, holder.resource)

  def get_containers(self, identifiers: Union[str, Sequence[int], Sequence[str]]) -> List[T]:
    """Get containers at multiple positions. Raises if any position is empty."""
    holders = self.get_items(identifiers)
    containers: List[T] = []
    for i, holder in enumerate(holders):
      if holder.resource is None:
        try:
          ident = self.get_child_identifier(holder)
        except (ValueError, AttributeError):
          ident = f"index {i}"
        raise ValueError(f"No container at position {ident} in rack '{self.name}'. ")
      containers.append(cast(T, holder.resource))
    return containers

  def has_container(self, identifier: Union[str, int]) -> bool:
    """Return True if a position is occupied."""
    holder = self.get_item(identifier)
    return holder.resource is not None

  def row_containers(self, row: Union[int, str]) -> List[T]:
    """Get all containers in a row. Raises if any position in the row is empty."""
    return self._collect_containers(self.row(row))

  def column_containers(self, col: int) -> List[T]:
    """Get all containers in a column. Raises if any position in the column is empty."""
    return self._collect_containers(self.column(col))

  def get_all_containers(self) -> List[T]:
    """Get all containers in order. Raises if any position is empty."""
    return self._collect_containers(self.get_all_items())

  def _collect_containers(self, holders: Sequence[ResourceHolder]) -> List[T]:
    containers: List[T] = []
    for holder in holders:
      if holder.resource is None:
        ident = self.get_child_identifier(holder)
        raise ValueError(f"No container at position {ident} in rack '{self.name}'. ")
      containers.append(cast(T, holder.resource))
    return containers

  def get_occupied_containers(self) -> List[T]:
    """Get all non-empty containers (only occupied positions)."""
    return [
      cast(T, holder.resource) for holder in self.get_all_items() if holder.resource is not None
    ]

  # =========================================================================
  # ASSIGNMENT
  # =========================================================================

  def __setitem__(
    self,
    identifier: Union[str, int, slice],
    value: Union[T, List[T], Sequence[T]],
  ) -> None:
    """Assign container(s) to position(s). Replaces existing contents."""
    if isinstance(identifier, slice) or (isinstance(identifier, str) and ":" in identifier):
      holders = self[identifier]

      if not isinstance(value, (list, tuple)):
        raise ValueError(
          f"When assigning to a range, value must be a list or tuple, not {type(value).__name__}"
        )

      if len(holders) != len(value):
        raise ValueError(
          f"Number of containers ({len(value)}) must match number of positions ({len(holders)})"
        )

      for container in value:
        self._check_content_type(container)
      for holder, container in zip(holders, value):
        self._assign_to_holder(holder, container)

    else:
      if isinstance(value, (list, tuple)):
        raise ValueError(
          f"Cannot assign list to single position '{identifier}'. "
          f"Use a range like 'A1:A3' for batch assignment."
        )

      assert isinstance(value, Resource)
      self._check_content_type(value)
      holder = self.get_item(identifier)
      self._assign_to_holder(holder, value)

  @classmethod
  def _check_content_type(cls, resource: Resource) -> None:
    if not isinstance(resource, cls._content_type):
      raise ValueError(
        f"Only {cls._content_type.__name__} resources can be added to a {cls.__name__}, "
        f"got {type(resource).__name__}."
      )

  @staticmethod
  def _assign_to_holder(holder: ResourceHolder, resource: Resource) -> None:
    # Resource.assign_child_resource only appends; without first unassigning the
    # existing occupant, reassigning a holder leaves the previous resource as
    # children[0] and the new one as children[1] (holder.resource still returns
    # the old one). Clear first so reassign actually replaces.
    if holder.resource is resource:
      return
    if holder.resource is not None:
      holder.unassign_child_resource(holder.resource)
    holder.assign_child_resource(resource, reassign=True)

  # =========================================================================
  # STATE MANAGEMENT
  # =========================================================================

  def empty(self) -> None:
    """Remove all containers from the rack."""
    for holder in self.get_all_items():
      if holder.resource is not None:
        holder.unassign_child_resource(holder.resource)

  @staticmethod
  def _occupied_func(item: ResourceHolder) -> str:
    return "O" if item.resource is not None else "-"
