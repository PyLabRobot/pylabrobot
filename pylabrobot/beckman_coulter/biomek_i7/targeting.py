"""From a PyLabRobot location on the deck to where a Biomek pod must put its first channel.

The chain, as STAR and Prep target their channels plus one Biomek correction:

1. a resource's location on the deck (`get_location_wrt(deck, ...)`, mm), plus the caller's offset;
2. into Biomek's frame (`deck.biomek_x` / `biomek_y`; Z is the deck z), in cm;
3. the pod's calibration of the deck position the resource stands on. Biomek moves each pod
   relative to that pod's own calibrated corner of the position (`DeckPosition.corner`, imported
   from the instrument file; corner n is pod n's), not to the nominal pin-grid position this
   package's deck model places it at. The difference, up to about 0.1 cm, is added.

What the pod's X/Y/Z name: mandrel A1 on a Multichannel and probe 1 on a Span-8, and the bare
channel's bottom for Z (design.md I9). The axis counts come from `PodConfiguration.to_counts`.
"""

from typing import Optional, Tuple

from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.beckman_coulter.biomek_i7.units import MM_PER_CM, BiomekPoint, to_biomek  # noqa: F401  (re-exported)


def deck_position_of(deck: BiomekI7Deck, resource: Resource) -> Optional[ResourceHolder]:
  """The deck position (a named site of an ALP: `P6`, `TL1`) the resource stands on, or is; None
  for a resource on no position."""
  names = set(deck.position_names)
  node: Optional[Resource] = resource
  while node is not None:
    if node.name in names and isinstance(node, ResourceHolder):
      return node
    node = node.parent
  return None


def position_correction(
  deck: BiomekI7Deck,
  configuration: DeviceConfiguration,
  position: ResourceHolder,
  pod_number: int,
) -> Tuple[float, float, float]:
  """How far, cm, pod `pod_number`'s calibrated corner of `position` is from where the deck model
  puts the position's corner (its back left bottom corner, in Biomek's frame). Zero for a position
  the configuration does not hold."""
  calibrated = configuration.deck_positions.get(position.name)
  if calibrated is None:
    return 0.0, 0.0, 0.0
  loc = position.get_location_wrt(deck)
  nominal = to_biomek(Coordinate(loc.x, loc.y + position.get_size_y(), loc.z))
  x, y, z = calibrated.corner(pod_number)
  return x - nominal.x, y - nominal.y, z - nominal.z


def target(
  deck: BiomekI7Deck,
  configuration: DeviceConfiguration,
  pod_number: int,
  resource: Resource,
  x: str = "c",
  y: str = "c",
  z: str = "b",
  offset: Coordinate = Coordinate.zero(),
) -> BiomekPoint:
  """Where pod `pod_number`'s first channel must be, in Biomek's frame, to be at the anchor (`x`,
  `y`, `z`, as `Resource.get_location_wrt` takes them) of `resource`, plus `offset` (mm): the
  resource's deck location, corrected by the pod's calibration of the deck position it stands on.

  Raises:
    ValueError: for a resource not on `deck`.
  """
  point = to_biomek(resource.get_location_wrt(deck, x=x, y=y, z=z) + offset)
  position = deck_position_of(deck, resource)
  if position is None:
    return point
  dx, dy, dz = position_correction(deck, configuration, position, pod_number)
  return BiomekPoint(point.x + dx, point.y + dy, point.z + dz)
