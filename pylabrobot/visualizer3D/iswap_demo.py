"""The iSWAP carrying a lidded plate between two carrier sites, and taking its lid off and on.

Run it:

    python -m pylabrobot.visualizer3D.iswap_demo

The arm is driven only by the PR's primitive moves - the X-arm, the head's Y and Z, the two joints
and the jaws - each of which the page plays at the speed the command carries. PyLabRobot does not
move what the iSWAP grips, so the page does: it hands the labware to the gripper when the jaws close
on it and to what is under it when they open - a site, or for a lid, a plate with no lid.

Until stopped: the plate goes over with its lid, the lid comes off onto the empty site, goes back
on, and the plate comes back.
"""

import asyncio
import logging
from typing import List

from pylabrobot.hamilton.star.motion import attach_viewer_motion
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import (
  cor_96_wellplate_360uL_Fb,
  cor_96_wellplate_360uL_Fb_lid,
)
from pylabrobot.resources.hamilton.plate_carriers import PLT_CAR_L5AC_A00
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource

from .demo import build_facility, star_of
from .server import Viewer3D

# How far above a plate's bottom the jaws take hold of it, in mm: below where its lid comes down
# over it, or they would close on the lid.
PLATE_GRIP_HEIGHT = 4.0
# How far above a lid's bottom the jaws take hold of it, in mm.
LID_GRIP_HEIGHT = 5.0
# How much narrower than what they hold the jaws close to, in mm: they stop on it.
SQUEEZE = 3.0


def grip_centre(iswap, deck: Resource) -> Coordinate:
  """Where the model has the point the jaws close on, in mm on the deck."""
  gripper = iswap.gripper
  local = (gripper.proximal_joint + gripper.tool_center_point).vector()
  turn = gripper.get_absolute_rotation().get_rotation_matrix()
  base = gripper.get_location_wrt(deck)
  moved = [sum(turn[row][k] * local[k] for k in range(3)) for row in range(3)]
  return Coordinate(base.x + moved[0], base.y + moved[1], base.z + moved[2])


async def move_grip_centre(iswap, deck: Resource, target: Coordinate, axes: str) -> None:
  """Bring the grip centre to `target` along the named axes, the joints held where they are."""
  offset = target - grip_centre(iswap, deck)
  if "x" in axes:
    x = await iswap.arm.request_position()
    await iswap.arm.move_to_x_position(round(x + offset.x, 1))
  if "y" in axes:
    y = await iswap.elbow_request_y_position()
    await iswap.elbow_move_to_y_position(round(y + offset.y, 1))
  if "z" in axes:
    z = await iswap.elbow_request_z_position()
    await iswap.elbow_move_to_z_position(round(z + offset.z, 1))


async def carry(iswap, deck: Resource, width: float, here: Coordinate, there: Coordinate, travel_z):
  """Take what is `width` wide and gripped at `here` to `there`, travelling at `travel_z`."""
  await iswap.gripper_open()
  await move_grip_centre(iswap, deck, Coordinate(here.x, here.y, travel_z), "xy")
  await move_grip_centre(iswap, deck, here, "z")
  await iswap.gripper_move_to_jaw_position(width - SQUEEZE)
  await move_grip_centre(iswap, deck, Coordinate(here.x, here.y, travel_z), "z")
  await move_grip_centre(iswap, deck, Coordinate(there.x, there.y, travel_z), "xy")
  await move_grip_centre(iswap, deck, there, "z")
  await iswap.gripper_open()
  await move_grip_centre(iswap, deck, Coordinate(there.x, there.y, travel_z), "z")


def surface(site: Resource, deck: Resource) -> Coordinate:
  """The middle of a site's surface, in mm on the deck."""
  return site.get_location_wrt(deck, "c", "c", "b")


def plate_grip(site: Resource, plate: Plate, deck: Resource) -> Coordinate:
  """Where the jaws close on `plate` standing on `site`."""
  seated = plate.location.z if plate.location is not None else 0.0
  return surface(site, deck) + Coordinate(0, 0, seated + PLATE_GRIP_HEIGHT)


def lid_grip_on_plate(site: Resource, plate: Plate, lid: Lid, deck: Resource) -> Coordinate:
  """Where the jaws close on `lid` covering `plate` on `site`: its bottom is the plate's top less
  the height it nests over it."""
  seated = plate.location.z if plate.location is not None else 0.0
  bottom = seated + plate.get_size_z() - lid.nesting_z_height
  return surface(site, deck) + Coordinate(0, 0, bottom + LID_GRIP_HEIGHT)


def lid_grip_on_site(site: Resource, deck: Resource) -> Coordinate:
  """Where the jaws close on a lid lying on an empty `site`."""
  return surface(site, deck) + Coordinate(0, 0, LID_GRIP_HEIGHT)


async def main() -> None:
  logging.disable(logging.WARNING)
  facility = build_facility(bare=True)
  star = star_of(facility)
  deck = star.deck

  # A carrier of its own on an otherwise empty deck, its two sites as far apart as the carrier
  # comes: the jaws open wider than what they grip, so everything around a move needs room for
  # the open jaws, and out here there is nothing to clip.
  carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
  deck.assign_child_resource(carrier, track=10)

  plate = cor_96_wellplate_360uL_Fb(name="plate")
  carrier[0] = plate
  lid = cor_96_wellplate_360uL_Fb_lid(name="plate_lid")
  plate.assign_child_resource(lid)
  await star.setup()
  iswap = star.iswap
  if iswap is None:
    raise RuntimeError("the simulated STARlet has no iSWAP")

  # The turned gripper reaches less far than the elbow's own travel: with the wrist at -135 the
  # grip centre hangs behind the elbow, so the farthest site the pose can bring the jaws to is the
  # elbow's front stop less that hang - site three, not site four.
  sites: List[Resource] = [plate.parent, carrier[3]]

  viewer = Viewer3D(facility, name="iswap_demo.py", open_browser=False)
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await viewer.wait_for_models()
  print("the page is up; the run starts in ten seconds")
  await asyncio.sleep(10.0)

  # Clear of the channels, then turned so the jaws close across the plate's short side. The elbow
  # is brought forward by the first carry itself: its reach envelope depends on the turn, so an
  # absolute jog from the folded park would guess wrong.
  await iswap.make_space()
  await iswap.rotate_to_angles(elbow_absolute_angle="front", gripper_absolute_angle="left")
  travel_z = grip_centre(iswap, deck).z

  width = plate.get_size_y()
  lid_width = lid.get_size_y()
  while True:
    start, other = sites
    print("the plate goes over, with its lid")
    await carry(
      iswap, deck, width, plate_grip(start, plate, deck), plate_grip(other, plate, deck), travel_z
    )
    print("its lid comes off, onto the site it left")
    await carry(
      iswap,
      deck,
      lid_width,
      lid_grip_on_plate(other, plate, lid, deck),
      lid_grip_on_site(start, deck),
      travel_z,
    )
    print("and goes back on")
    await carry(
      iswap,
      deck,
      lid_width,
      lid_grip_on_site(start, deck),
      lid_grip_on_plate(other, plate, lid, deck),
      travel_z,
    )
    print("the plate comes back")
    await carry(
      iswap, deck, width, plate_grip(other, plate, deck), plate_grip(start, plate, deck), travel_z
    )
    await asyncio.sleep(1.0)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
