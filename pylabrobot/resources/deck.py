from __future__ import annotations

from typing import Any, List, Mapping, Optional

from pylabrobot.resources.errors import ResourceNotFoundError

from .coordinate import Coordinate
from .resource import Resource
from .trash import Trash


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
    *,
    identifier: Optional[str] = None,
  ):
    """Initialize a new deck."""

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      metadata=metadata,
      identifier=identifier,
    )
    self.location = origin

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

    for resource in self.children.copy():
      if isinstance(resource, Trash) and not include_trash:
        continue
      resource.unassign()

  def get_trash_area(self) -> Trash:
    """Returns the trash area resource."""
    matches = [
      resource
      for resource in self.get_all_resources()
      if isinstance(resource, Trash) and resource.category not in {"trash96", "trash_container"}
    ]
    if not matches:
      raise ResourceNotFoundError("Trash area not found")
    if len(matches) > 1:
      raise ValueError("Multiple trash areas found; specify the resource explicitly.")
    return matches[0]

  def summary(self) -> str:
    """Returns a summary of the deck layout."""
    summary_ = f"Deck: {self.get_absolute_size_x()} x {self.get_absolute_size_y()} mm\n\n"
    for resource in self.children:
      summary_ += f"{resource.name}: {resource}\n"
    return summary_

  def get_trash_area96(self) -> Trash:
    deck_class = self.__class__.__name__
    raise NotImplementedError(f"This method is not implemented by deck '{deck_class}'")
