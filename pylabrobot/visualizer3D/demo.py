"""A facility with a v1 STAR in it, plus a bench that is not a device at all.

Run it:

    python -m pylabrobot.visualizer3D.demo

The world is a `Facility`. A bench stands at its origin and a simulated `STARDevice` stands on the
bench. The viewer treats them identically, because it never asks what anything is.

The v1 STAR has no aspirate or dispense yet, so the run below drives the volume trackers
directly. That is the same channel the viewer subscribes to either way: a real pipetting command
would move these trackers rather than talk to the viewer.
"""

import asyncio
import itertools
import logging

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
  hamilton_mfx_module_tiprackholder_ntr,
  hamilton_mfx_tiprackholder_standard,
  hamilton_tip_carrier_L5_ntr_a00,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource

from .facility import Facility
from .server import Viewer3D

logging.disable(logging.WARNING)


def star_of(facility: Resource) -> STARDevice:
  """The STAR the demo drives. It is the first thing assigned, and the only device here."""
  star = facility.children[0]
  if not isinstance(star, STARDevice):
    raise TypeError(f"expected a STAR at the front of {facility.name}, found {type(star).__name__}")
  return star


def build_facility() -> Facility:
  """A facility with a bench in it and a STAR standing on the bench."""
  facility = Facility(name="facility", size_x=2600, size_y=1400, size_z=1000)

  # A bench is not a device, has no deck and no driver, and still takes part in the same
  # cartesian space. This is the case the old visualizer had no way to express. It stands at the
  # facility's origin and the STAR stands on it. The STAR is assigned first all the same, since
  # `star_of` finds it as the facility's first child.
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
    0: hamilton_mfx_module_tiprackholder_ntr(name="mfx_ntr4_0"),
    1: hamilton_mfx_module_tiprackholder_ntr(name="mfx_ntr4_1"),
    2: hamilton_mfx_module_tiprackholder_ntr(name="mfx_ntr4_2"),
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

  # The deck's own `size_z` is 900 mm, taken from the instrument's configuration file. That is the
  # working envelope, not the deck's extent, and it leaves the deck protruding 75.5 mm through the
  # roof of the device carrying it while enclosing 665 mm of empty space. The honest height is the
  # top of the X-arm that rides above it: 334.7 mm of channel travel plus the arm's own 140 mm.
  # Applied here rather than upstream, since this viewer does not edit the resource library.
  DECK_HEIGHT = 334.7 + 140.0
  star.deck._size_z = DECK_HEIGHT
  star.deck._local_size_z = DECK_HEIGHT

  return facility


def declare_channel_access(star) -> None:
  """Record how far the channels reach across the deck, once the arm has been discovered.

  Depths of 465, 393 and 321 mm, concentric: each narrower band is inset by half the difference at
  both ends, so every extra four channels costs 36 mm of reach front and back - four channels at
  the 9 mm pitch. In Y this is the deck's own frame, which is also the frame the instrument is
  commanded in, since the backend derives every firmware coordinate from `get_location_wrt(deck)`.

  In X the bands stop where the channels do, which neither the arm's travel nor the deck edge
  describes: both run further right than the channels ever go. The waste block is the real
  right-hand limit on this device - the channels eject into it and go no further - so the band
  stops at its near edge. Nothing reports that limit, which is why it is looked up here.

  Declared here because no v1 capability publishes any of it; it belongs on the pipettes
  capability, whose reach it describes, rather than on the deck it is measured across.
  """
  low, high = star.x_arm.configuration.x_range
  x_from = max(0.0, low)
  x_to = min(star.deck.get_absolute_size_x(), high)

  # Where the device's x refers to is the arm's own to say, and the deck states it when it places
  # the arm: 223.00 mm from its left edge, measured on the part. This used to derive it here as
  # half the arm's width, from a time when the arm's width was the width its drive reports and the
  # tracked point was the middle of it. It is neither, and half of 400.49 mm put the mark 22.755 mm
  # left of where the drive says the arm is.
  if star.x_arm.resource is not None:
    # The opening through the carriage, used only where no model is drawn for the arm.
    if star.x_arm.configuration.reference_point == "center":
      star.x_arm.resource.window = {"width": 185.0, "inset_y": 20.0}

  waste_block = star.deck.waste_block
  if waste_block is not None:
    x_to = min(x_to, waste_block.get_location_wrt(star.deck).x)

  star.deck.access_bands = [
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


async def sweep_arm(star) -> None:
  """Walk the X-arm back and forth, so the viewer has a moving part to follow.

  The arm is the one thing on a v1 STAR that reports a live position, so this is what a viewer
  tracking motion actually has to work from.
  """
  low, high = star.x_arm.configuration.x_range
  span = min(high, star.deck.get_absolute_size_x()) - low
  # Until it is stopped. A count rather than a large number of steps, which is the same loop
  # wearing a bound it never reaches.
  for step in itertools.count():
    target = low + span * (0.15 if step % 2 else 0.75)
    await star.x_arm.move_x(round(target, 1))
    await asyncio.sleep(3.0)


# How far forward to bring the rotation drive before turning it, in mm. Enough to clear link 1's
# own length, so the whole swing has room.
ROOM_TO_TURN = 200.0


async def work_the_iswap(star) -> None:
  """Turn the arm's two joints and work its jaws, so the viewer has the whole linkage moving.

  Every pose is one the arm reports back afterwards, so what the viewer draws is the model
  following the drives rather than a path written here. The poses are the stops the arm stores,
  which is what it is calibrated against, and the widths are the ends of the gripper's own travel.
  """
  iswap = star.iswap
  if iswap is None:
    return

  # Cleared once, up front. Parked at the back of its travel the arm cannot turn far: a quarter
  # turn puts the grip centre further back than the drive itself reaches, and the guard refuses it -
  # which is right, and which left half of the poses below doing nothing at all. Clearing more than
  # link 1's length leaves room for the whole swing.
  await iswap.make_space()
  parked = await iswap.rotation_drive_request_y_position()
  try:
    await iswap.rotation_drive_move_to_y_position(parked - ROOM_TO_TURN)
  except ValueError as refused:
    print(f"  the drive stays where it is, so the arm will not turn far: {refused}")

  c = iswap.configuration
  rotation_stops = ["front", "left", "front", "right"]
  gripper_directions = ["front", "back", "front", "back"]
  jaws = [
    c.gripper_increments_to_mm(c.gripper_range_increments[1]),
    c.gripper_increments_to_mm(c.gripper_range_increments[0]),
  ]

  for step in itertools.count():
    rotation = rotation_stops[step % len(rotation_stops)]
    gripper = gripper_directions[step % len(gripper_directions)]
    try:
      await iswap.rotate_to_angles(
        rotation_absolute_angle=rotation, gripper_absolute_angle=gripper, raise_features=True
      )
    except ValueError as refused:
      # The guards stand between the arm and the channels, and a demo is not a reason to talk
      # past them: what they refuse is what a real caller would be refused.
      # Printed rather than logged: nothing configures logging here, so an `info` call is a
      # refusal nobody sees - and a pose silently not happening looks like a viewer that has
      # stopped drawing.
      print(f"  the arm may not go to {rotation}/{gripper}: {refused}")
    await asyncio.sleep(2.5)

    await iswap.gripper_move_to_jaw_position(jaws[step % len(jaws)])
    await asyncio.sleep(2.5)


async def main() -> None:
  set_volume_tracking(True)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  declare_channel_access(star)

  viewer = Viewer3D(facility, name="demo.py")
  await viewer.start()

  await asyncio.sleep(1.5)  # let a browser connect before anything moves
  await run(facility)
  # The arm and the iSWAP move at once, as they do on the device: they are on the same carriage
  # and neither waits for the other.
  await asyncio.gather(sweep_arm(star), work_the_iswap(star))


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
