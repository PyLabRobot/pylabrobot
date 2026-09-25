"""A facility with a simulated STAR and a bench in it.

Run it:

    python -m pylabrobot.visualizer3D.demo

The run fills a plate, moves liquid to another plate through the volume trackers, then sweeps
the X-arm and works the iSWAP for as long as it runs. `model_demo` shares the facility, the STAR
lookup and the iSWAP loops defined here.
"""

import asyncio
import itertools
import logging
from typing import Tuple

from pylabrobot.hamilton.star.device import STARDevice, STARLet
from pylabrobot.resources import set_volume_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  hamilton_96_tiprack_10uL_NTR,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_300uL_filter_slim,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_1000uL_filter,
  hamilton_mfx_carrier_L5_base,
  hamilton_mfx_resourceholder_ntr,
  hamilton_mfx_tiprackholder_standard,
  hamilton_tip_carrier_L5_ntr_a00,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource

from .facility import Facility
from .server import Viewer3D


def star_of(facility: Resource) -> STARDevice:
  """The STAR the demo drives: the facility's first child."""
  star = facility.children[0]
  if not isinstance(star, STARDevice):
    raise TypeError(f"expected a STAR at the front of {facility.name}, found {type(star).__name__}")
  return star


def build_facility(*, bare: bool = False) -> Facility:
  """A facility with a simulated STARLet standing on a bench, with labware on its deck.

  Args:
    bare: the STARLet alone at the origin, with nothing else to look at.
  """
  if bare:
    facility = Facility(name="facility", size_x=2000, size_y=1200, size_z=1000)
    facility.assign_child_resource(STARLet(simulation=True), location=Coordinate(0, 0, 0))
    return facility

  facility = Facility(name="facility", size_x=2600, size_y=1400, size_z=1000)

  # A bench: no deck, no driver, the same cartesian space. The STAR is assigned first all the
  # same, since `star_of` finds it as the facility's first child.
  bench = Resource(name="bench", size_x=900, size_y=600, size_z=880, category="bench")

  star = STARLet(simulation=True)
  facility.assign_child_resource(star, location=Coordinate(0, 0, bench.get_size_z()))
  facility.assign_child_resource(bench, location=Coordinate(0, 0, 0))

  tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
  # Filtered and unfiltered side by side, so the filter in a tip can be told apart.
  tip_carrier[0] = hamilton_96_tiprack_1000uL(name="tips_0")
  tip_carrier[1] = hamilton_96_tiprack_1000uL_filter(name="tips_1")
  tip_carrier[2] = hamilton_96_tiprack_300uL_filter(name="tips_2")
  star.deck.assign_child_resource(tip_carrier, rails=1)

  plate_carrier = PLT_CAR_L5AC_A00(name="source_carrier")
  for slot in range(5):
    plate_carrier[slot] = cor_96_wellplate_360uL_Fb(name=f"source_{slot}")
  star.deck.assign_child_resource(plate_carrier, track=7)

  destination_carrier = PLT_CAR_L5AC_A00(name="destination_carrier")
  for slot in range(5):
    destination_carrier[slot] = cor_96_wellplate_360uL_Fb(name=f"destination_{slot}")
  star.deck.assign_child_resource(destination_carrier, track=13)

  ntr_carrier = hamilton_tip_carrier_L5_ntr_a00(name="ntr_carrier")
  ntr_carrier[0] = hamilton_96_tiprack_10uL_NTR(name="ntr_10uL")
  ntr_carrier[1] = hamilton_96_tiprack_50uL_NTR(name="ntr_50uL")
  ntr_carrier[2] = hamilton_96_tiprack_300uL_NTR(name="ntr_300uL")
  star.deck.assign_child_resource(ntr_carrier, track=19)

  # The same nested racks on MFX NTR4 modules, and two MFX tip modules holding framed racks: the
  # last six tracks, up to the waste block.
  modules = {
    0: hamilton_mfx_resourceholder_ntr(name="mfx_ntr4_0"),
    1: hamilton_mfx_resourceholder_ntr(name="mfx_ntr4_1"),
    2: hamilton_mfx_resourceholder_ntr(name="mfx_ntr4_2"),
    3: hamilton_mfx_tiprackholder_standard(name="mfx_tiprack_holder_3"),
    4: hamilton_mfx_tiprackholder_standard(name="mfx_tiprack_holder_4"),
  }
  mfx_carrier = hamilton_mfx_carrier_L5_base(name="mfx_carrier", modules=modules)
  modules[0].assign_child_resource(hamilton_96_tiprack_10uL_NTR(name="mfx_ntr_10uL"))
  modules[1].assign_child_resource(hamilton_96_tiprack_50uL_NTR(name="mfx_ntr_50uL"))
  modules[2].assign_child_resource(hamilton_96_tiprack_300uL_NTR(name="mfx_ntr_300uL"))
  modules[3].assign_child_resource(hamilton_96_tiprack_1000uL_filter(name="mfx_tips_1000uL_filter"))
  modules[4].assign_child_resource(
    hamilton_96_tiprack_300uL_filter_slim(name="mfx_tips_300uL_filter_slim")
  )
  star.deck.assign_child_resource(mfx_carrier, track=25)

  # The deck's own `size_z` (900 mm) is the working envelope, not its extent. The top of the X-arm
  # riding above it is the honest height: channel travel plus the arm's own height.
  DECK_HEIGHT = 334.7 + 140.0
  star.deck._size_z = DECK_HEIGHT
  star.deck._local_size_z = DECK_HEIGHT

  return facility


def x_travel(star: STARDevice) -> Tuple[float, float]:
  """The X-arm's travel as (low, high) in mm, known once `setup()` has discovered the arm."""
  x_range = star.x_arm.configuration.x_range
  if x_range is None:
    raise RuntimeError("the X-arm's travel is only known after setup")
  return x_range


def declare_channel_access(star: STARDevice) -> None:
  """Declare the channels' reach across the deck as `access_bands`, once the arm is set up.

  Depths of 465, 393 and 321 mm for 4/8, 12 and 16 channels, concentric, in the deck's frame. In
  X the bands run from the arm's travel to the waste block, where the channels stop. Declared
  here because no capability publishes it.
  """
  low, high = x_travel(star)
  x_from = max(0.0, low)
  x_to = min(star.deck.get_absolute_size_x(), high)

  # Where the device's x refers to is the arm's own to say, and the deck states it when it places
  # the arm: nothing about the reference point is derived here.
  if star.x_arm.resource is not None:
    # The opening through the carriage, used only where no model is drawn for the arm.
    if star.x_arm.configuration.reference_point == "center":
      star.x_arm.resource.window = {"width": 185.0, "inset_y": 20.0}  # type: ignore[attr-defined]

  waste_block_name = star.deck.get_component_name("waste_block")
  if star.deck.has_resource(waste_block_name):
    waste_block = star.deck.get_resource(waste_block_name)
    x_to = min(x_to, waste_block.get_location_wrt(star.deck).x)

  # A field the viewer reads off the deck; `Resource` does not declare it.
  star.deck.access_bands = [  # type: ignore[attr-defined]
    {"label": label, "from": front, "to": front + depth, "x_from": x_from, "x_to": x_to}
    for label, front, depth in (
      ("4/8", 77.5, 465.0),
      ("12", 113.5, 393.0),
      ("16", 149.5, 321.0),
    )
  ]


def fill(plate: Plate, volume: float) -> None:
  for well in plate.get_all_items():
    well.tracker.set_volume(volume)


async def run(facility: Resource) -> None:
  """Move liquid around so the state channel has something to carry."""
  star = star_of(facility)
  source = star.deck.get_resource("source_0")
  destination = star.deck.get_resource("destination_0")
  if not isinstance(source, Plate) or not isinstance(destination, Plate):
    raise TypeError("the demo deck no longer holds the plates this run drives")

  fill(source, 300.0)
  await asyncio.sleep(2.0)

  for column in range(12):
    for row in "ABCDEFGH":
      well = f"{row}{column + 1}"
      taken = min(150.0, source.get_item(well).tracker.get_used_volume())
      source.get_item(well).tracker.remove_liquid(taken)
      destination.get_item(well).tracker.add_liquid(volume=taken)
    await asyncio.sleep(0.45)

  print("run complete; the viewer stays up")


async def sweep_arm(star: STARDevice) -> None:
  """Walk the X-arm back and forth until stopped, so the viewer has a moving part to follow.

  The arm is the one part of the STAR that reports a live position.
  """
  low, high = x_travel(star)
  span = min(high, star.deck.get_absolute_size_x()) - low
  # Until it is stopped. A count rather than a large number of steps, which is the same loop
  # wearing a bound it never reaches.
  for step in itertools.count():
    target = low + span * (0.15 if step % 2 else 0.75)
    await star.x_arm.move_to_x_position(round(target, 1))
    await asyncio.sleep(3.0)


# How far forward to bring the elbow before turning it, in mm: more than link 1's own length,
# so the whole swing has room.
ROOM_TO_TURN = 200.0

# How long each pose is held, in seconds, so a move can be followed rather than glimpsed.
HELD_FOR = 2.5


async def turn_the_arm(star: STARDevice) -> None:
  """Swing the iSWAP's elbow between its stops until stopped, the wrist pointing front.

  Clears the deck once, up front, and brings the elbow forward by `ROOM_TO_TURN` first.
  """
  iswap = star.iswap
  if iswap is None:
    return

  # Cleared once, up front: a rotation cannot clear the deck for itself, so the channels and the
  # head are put out of the way first and stay there for the run.
  await iswap.make_space()

  # Parked at the back of its travel, a quarter turn puts the grip centre behind the drive's own
  # reach and the guard refuses it; link 1's length of room lets it swing.
  parked = await iswap.elbow_request_y_position()
  try:
    await iswap.elbow_move_to_y_position(parked - ROOM_TO_TURN)
  except ValueError as refused:
    print(f"  the drive stays where it is, so the arm will not turn far: {refused}")

  # Until it is stopped. A count rather than a large number of steps, which is the same loop
  # wearing a bound it never reaches.
  for step in itertools.count():
    where = ("front", "left", "front", "right")[step % 4]
    try:
      await iswap.rotate_to_angles(elbow_absolute_angle=where, gripper_absolute_angle="front")
    except ValueError as refused:
      # What the guards refuse is what a real caller would be refused. Printed, not logged: nothing
      # configures logging here, and a pose silently not happening looks like a viewer that stopped.
      print(f"  the arm may not go to {where}: {refused}")
    await asyncio.sleep(HELD_FOR)


async def work_the_jaws(star: STARDevice) -> None:
  """Open and close the gripper until stopped.

  The stops are the ends of the drive's own travel: widest first, offset by half a hold against
  the arm's cycle.
  """
  iswap = star.iswap
  if iswap is None:
    return

  c = iswap.configuration
  # Widest first: the arm comes up holding whatever it homed at, and opening from there reads as a
  # move where closing onto an already-closed gripper would not.
  closed, opened = (c.gripper_increments_to_mm(end) for end in c.gripper_range_increments)
  # Offset against the arm's own cycle, so the two are not seen only ever moving together.
  await asyncio.sleep(HELD_FOR / 2)

  for step in itertools.count():
    width = (opened, closed)[step % 2]
    try:
      await iswap.gripper_move_to_jaw_position(width)
    except ValueError as refused:
      print(f"  the jaws may not go to {width:.1f} mm: {refused}")
    await asyncio.sleep(HELD_FOR)


async def main() -> None:
  logging.disable(logging.WARNING)
  set_volume_tracking(True)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  declare_channel_access(star)

  viewer = Viewer3D(facility, name="demo.py")
  await viewer.start()

  await viewer.wait_for_browser()  # nothing moves before a page is drawing
  await run(facility)
  # The arm and the iSWAP move at once, as they do on the device: they are on the same carriage
  # and neither waits for the other.
  await asyncio.gather(sweep_arm(star), turn_the_arm(star), work_the_jaws(star))


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
