from __future__ import annotations

from typing import Any, List, Mapping, Optional, Type, TypeVar, cast

from pylabrobot.resources.errors import ResourceNotFoundError

from .coordinate import Coordinate
from .resource import Resource
from .trash import Trash

T = TypeVar("T", bound=Resource)


def _built(resources: List[Resource], suffix: str, cls: Type[T]) -> Optional[T]:
  """The resource a deck built and called `suffix`, whatever prefix it gave it.

  Found by what it is called rather than held as an attribute, so a deck read back from a file
  finds it too.
  """
  for resource in resources:
    if isinstance(resource, cls) and (
      resource.name == suffix or resource.name.endswith(f"_{suffix}")
    ):
      return resource
  return None


class Deck(Resource):
  """Base class for liquid handler decks."""

  def __init__(
    self,
    size_x: float,
    size_y: float,
    size_z: float,
    name: str = "deck",
    origin: Coordinate = Coordinate(0, 0, 0),
    category: str = "deck",
    metadata: Optional[Mapping[str, Any]] = None,
    prefix: Optional[str] = None,
  ):
    """Initialize a new deck.

    `prefix` is what this deck names the resources it owns after: the device it belongs to, so two
    devices stand in one tree without their names colliding. It defaults to this deck's own name,
    without the `_Deck` it may end in.
    """

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      metadata=metadata,
    )
    self.location = origin
    if prefix is None:
      prefix = name[: -len("_Deck")] if name.endswith("_Deck") else name
    self.prefix = prefix

  def prefixed(self, name: str) -> str:
    """`name` under this deck's prefix, which is what the device owning the deck is called."""
    return f"{self.prefix}_{name}"

  def serialize(self) -> dict:
    """Serialize this deck."""
    super_serialized = super().serialize()
    super_serialized.pop("model", None)  # deck's don't typically have a model
    return super_serialized

  def get_all_resources(self) -> List[Resource]:
    """Returns a list of all resources in the deck."""
    return self.get_all_children()

  def clear(self, include_trash: bool = False):
    """Removes all resources from the deck.

    Examples:
      Clearing all resources on a liquid handler deck:

      >>> lh.deck.clear()

      Clearing all resources on a liquid handler deck, including the trash area:

      >>> lh.deck.clear(include_trash=True)
    """

    children_names = [child.name for child in self.children]
    for resource_name in children_names:
      resource = self.get_resource(resource_name)
      if isinstance(resource, Trash) and not include_trash:
        continue
      resource.unassign()

  def get_trash_area(self) -> Trash:
    """Returns the trash area resource."""
    if not self.has_resource("trash"):
      raise ResourceNotFoundError("Trash area not found")
    return cast(Trash, self.get_resource("trash"))

  def summary(self) -> str:
    """Returns a summary of the deck layout."""
    summary_ = f"Deck: {self.get_absolute_size_x()} x {self.get_absolute_size_y()} mm\n\n"
    for resource in self.children:
      summary_ += f"{resource.name}: {resource}\n"
    return summary_

  def get_trash_area96(self) -> Trash:
    deck_class = self.__class__.__name__
    raise NotImplementedError(f"This method is not implemented by deck '{deck_class}'")
