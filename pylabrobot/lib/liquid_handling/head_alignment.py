"""One head, one grid of items: which item is under which channel, and what a tip command does.

With one channel placed over one item, every other channel's item follows from the head's and the
grid's rows and columns, the grid's rotation in the resource tree, and how many items lie between
two channels (1 for a tip rack or a 96 plate, 2 for 384, 4 for 1536).

On that rests the check of a 96-head tip command, made before anything moves: a pick-up is refused
if the head already carries tips or no channel stands over a tip; a drop if a tip would land on a
tip or beside the rack; either if the head would come down on the rack's frame. Once the device has
done it, the caller moves each tip between the spot and the channel of each returned pair.

Dropping into a rack needs tip tracking on every spot under a tip: a tip on a tip can only be ruled
out against a tracked rack. Picking up does not: an untracked spot hands out a fresh tip and is left
as it is.
"""

from typing import List, Optional, Tuple, TypeVar

from pylabrobot.resources import Coordinate, ItemizedResource, Resource, TipRack, TipSpot
from pylabrobot.resources.n_channel_pipettes import NChannelPipette, TipMountingShaft
from pylabrobot.resources.utils import label_to_row_index, split_identifier

T = TypeVar("T", bound=Resource)

# How far the head's last channel may stand off its item when the pitches do not quite match, in mm.
PITCH_TOLERANCE = 0.5

# Per quarter turn about z: how one grid row and one grid column step on the deck, as (row, column).
# Deck rows run front-wards, columns right-wards; a turn is counter-clockwise seen from above.
_DECK_STEPS = {
  0: ((1, 0), (0, 1)),
  1: ((0, 1), (-1, 0)),
  2: ((-1, 0), (0, -1)),
  3: ((0, -1), (1, 0)),
}


def _parse_identifier(identifier: str) -> Tuple[int, int]:
  """'D6' as (row, column), zero-based: (3, 5)."""
  row_label, column_label = split_identifier(identifier)
  return label_to_row_index(row_label), int(column_label) - 1


def get_items_under_channels(
  head: NChannelPipette, grid: ItemizedResource[T], channel: str, item: str
) -> List[List[Optional[T]]]:
  """The item of `grid` under each head channel with `channel` over `item`; None past its edge.

  Args:
    head: the head, whose rows, columns and channel pitch are read.
    grid: a tip rack or a plate on the head's pitch, or a whole fraction of it.
    channel: the head channel placed over `item`, e.g. "H12".
    item: the item it is placed over, e.g. "D6".

  Returns:
    One row per head row, one entry per channel.

  Raises:
    ValueError: If the grid is turned by other than a multiple of 90 degrees about z, or tilted,
      or its pitch does not match the head's within `PITCH_TOLERANCE`.
  """
  rotation = grid.get_absolute_rotation()
  if rotation.x % 360 or rotation.y % 360 or rotation.z % 90:
    raise ValueError(f"{grid.name} is not square to the deck: rotation {rotation}")
  row_step, column_step = _DECK_STEPS[round(rotation.z / 90) % 4]

  # Each of these reads walks the whole grid, so they are read once.
  head_rows, head_columns = head.num_items_y, head.num_items_x
  grid_rows, grid_columns = grid.num_items_y, grid.num_items_x
  head_span = max(head_rows, head_columns) - 1
  pitch = grid.item_dx
  if abs(pitch - grid.item_dy) * head_span > PITCH_TOLERANCE:
    raise ValueError(f"{grid.name}'s rows and columns are spaced differently")
  stride = round(head.channel_pitch / pitch)
  drift = abs(head.channel_pitch - stride * pitch) * head_span
  if stride < 1 or drift > PITCH_TOLERANCE:
    raise ValueError(
      f"a {head.channel_pitch} mm head over {grid.name}: its last channel would stand "
      f"{drift:.2f} mm off its item"
    )

  channel_row, channel_column = _parse_identifier(channel)
  item_row, item_column = _parse_identifier(item)
  under: List[List[Optional[T]]] = []
  for head_row in range(head_rows):
    row: List[Optional[T]] = []
    for head_column in range(head_columns):
      # How far this channel is from `channel` on the deck, turned back into the grid's own rows
      # and columns: the steps are unit vectors, so turning back is a dot product with each.
      deck_rows = (head_row - channel_row) * stride
      deck_columns = (head_column - channel_column) * stride
      r = item_row + deck_rows * row_step[0] + deck_columns * row_step[1]
      c = item_column + deck_rows * column_step[0] + deck_columns * column_step[1]
      inside = 0 <= r < grid_rows and 0 <= c < grid_columns
      row.append(grid.get_item((r, c)) if inside else None)
    under.append(row)
  return under


def get_a1_coordinate(
  head: NChannelPipette, grid: ItemizedResource, deck: Resource, channel: str, item: str
) -> Coordinate:
  """Where head channel A1 goes with `channel` over the centre of `item`, in deck mm.

  The Z is the item's bottom.
  """
  centre = grid.get_item(item).get_location_wrt(deck, "c", "c", "b")
  row, column = _parse_identifier(channel)
  return Coordinate(
    centre.x - column * head.channel_pitch, centre.y + row * head.channel_pitch, centre.z
  )


def _get_spots_under_shafts(
  head: NChannelPipette, rack: TipRack, channel: str, tip_spot: str
) -> List[Tuple[TipMountingShaft, Optional[TipSpot]]]:
  """Each head channel with the spot under it, None past the rack's edge."""
  under = get_items_under_channels(head, rack, channel, tip_spot)
  return [
    (head.get_item((row, column)), spot)
    for row, spots in enumerate(under)
    for column, spot in enumerate(spots)
  ]


def _channels(head: NChannelPipette, shafts: List[TipMountingShaft]) -> str:
  """The channels, for a message: "all channels", or "channels ['H11', 'H12']"."""
  if len(shafts) == head.num_items:
    return "all channels"
  return f"channels {[head.get_child_identifier(shaft) for shaft in shafts]}"


def _refuse_if(action: str, problems: List[str]) -> None:
  """Raise, naming every problem, if there are any.

  Raises:
    ValueError: If `problems` is not empty.
  """
  if problems:
    raise ValueError(f"{action} refused: " + "; ".join(problems))


def plan_pick_up(
  head: NChannelPipette, rack: TipRack, channel: str, tip_spot: str
) -> List[Tuple[TipMountingShaft, TipSpot]]:
  """Check picking up from `rack` with head `channel` over `tip_spot`; each channel and its spot.

  Tip tracking is not needed: an untracked spot gives a tip whatever the model holds.

  Returns:
    Each channel that takes a tip, with the spot it takes it from.

  Raises:
    ValueError: Naming every problem: tips already on the head, no channel over a tip, or the head
      coming down on the rack's frame; or if a channel stands past the rack's edge and the rack
      does not declare its `frame_height`.
  """
  under = _get_spots_under_shafts(head, rack, channel, tip_spot)
  problems: List[str] = []
  # Only a channel past the rack's edge brings the head down beside the spots, onto any frame.
  if any(spot is None for _, spot in under) and rack.frame_height != 0:
    problems.append(f"the head would come down on the frame of {rack.name}")
  carrying = [shaft for shaft, _ in under if shaft.has_tip()]
  if carrying:
    problems.append(f"{_channels(head, carrying)} already carry a tip")
  pairs = [
    (shaft, spot)
    for shaft, spot in under
    if spot is not None and (not spot.tracks_tips or spot.tip is not None)
  ]
  if not pairs:
    problems.append("no channel stands over a tip")
  _refuse_if("pick-up", problems)
  return pairs


def plan_drop(
  head: NChannelPipette, rack: TipRack, channel: str, tip_spot: str
) -> List[Tuple[TipMountingShaft, TipSpot]]:
  """Check dropping the head's tips into `rack` with head `channel` over `tip_spot`.

  Needs tip tracking on every spot that would take a tip.

  Returns:
    Each channel that carries a tip, with the spot its tip goes into.

  Raises:
    ValueError: Naming every problem: no tips on the head, a tip over a spot that does not track
      tips, a tip on a tip, a tip beside the rack, or the head coming down on the rack's frame; or
      if a channel stands past the rack's edge and the rack does not declare its `frame_height`.
  """
  under = _get_spots_under_shafts(head, rack, channel, tip_spot)
  problems: List[str] = []
  # Only a channel past the rack's edge brings the head down beside the spots, onto any frame.
  if any(spot is None for _, spot in under) and rack.frame_height != 0:
    problems.append(f"the head would come down on the frame of {rack.name}")
  carrying = [(shaft, spot) for shaft, spot in under if shaft.has_tip()]
  if not carrying:
    problems.append("the head carries no tips")
  untracked = [s for s, spot in carrying if spot is not None and not spot.tracks_tips]
  on_tip = [
    s for s, spot in carrying if spot is not None and spot.tracks_tips and spot.tip is not None
  ]
  beside = [s for s, spot in carrying if spot is None]
  if untracked:
    problems.append(
      f"{_channels(head, untracked)} are over spots of {rack.name} that do not track tips;"
      " dropping into a tip rack needs tip tracking (set_tip_tracking(True))"
    )
  if on_tip:
    problems.append(f"{_channels(head, on_tip)} would put a tip on a tip")
  if beside:
    problems.append(f"{_channels(head, beside)} would drop a tip beside {rack.name}")
  _refuse_if("drop", problems)
  return [(shaft, spot) for shaft, spot in carrying if spot is not None]
