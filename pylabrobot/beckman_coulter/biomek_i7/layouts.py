"""Putting a method's labware on a Biomek i7 deck: `place_labware` places a Biomek Instrument
Setup's labware classes on the deck positions it names. The deck itself (which ALPs stand where)
is each instrument's: build it with `BiomekI7Deck.assign_alp`, by Biomek's own `column` (an ALP's
left pins) and `row` (its front pins), as the instrument file's `Decks/<deck>/Groups/<ALP>` gives
them."""

from typing import Iterable, List, Mapping, Optional

from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_labware


def place_labware(deck: BiomekI7Deck, setup: Iterable[Mapping[str, Optional[str]]]) -> List[str]:
  """Put Biomek labware on `deck` as a method's Instrument Setup step places it: each item a
  `position` (Biomek's name, `P6`, `TL1`), the labware `class` (`Thermo96VB_PP`, `BC1025F`) and the
  `name` the method gives it (the position's name when it gives none; `<name>_<position>` when
  two share one), and optionally a `lid` class to seat on it (or "default"). Each class is its PyLabRobot
  definition (`resource_model/labware.py` names them); a position's labware is centred in it, as
  Biomek does.

  Returns:
    What was left out and why: a class not built yet (a tube rack, a lid), or a position the deck
    does not have.

  Raises:
    ValueError: for an item without a position or a class.
  """
  skipped = []
  names: set = set()
  for item in setup:
    position, cls = item.get("position"), item.get("class")
    if not position or not cls:
      raise ValueError(f"a setup item needs a position and a class: {dict(item)}")
    # Resource names are unique in a tree; a method may give two positions the same one.
    name = item.get("name") or position
    if name in names:
      name = f"{name}_{position}"
    names.add(name)
    if position not in deck.position_names:
      skipped.append(f"{position}: no such position on {deck.name}")
      continue
    try:
      labware = biomek_labware(cls, name, lid=item.get("lid"))
    except NotImplementedError as e:
      skipped.append(f"{position}: {e}")
      continue
    deck.get_position(position).assign_child_resource(labware)
  return skipped
