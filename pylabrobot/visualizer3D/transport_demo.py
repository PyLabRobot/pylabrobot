"""The iSWAP moving a lidded plate and its lid with `iSWAPTransport`, the model kept as it goes.

Run it:

    python -m pylabrobot.visualizer3D.transport_demo

Each move is planned from the resources - where they are, which side they are gripped from - and
made of the iSWAP's primitive moves, which the page plays. The model hands what is gripped to the
gripper when the jaws close and to where it goes when they open, so the page shows what PyLabRobot
holds.

Until stopped: the plate goes over with its lid, gripped from the front; the lid comes off onto the
empty site and goes back on; the plate comes back gripped from the back, and so turned round, and
is turned back.
"""

import asyncio
import logging

from pylabrobot.hamilton.star.driver.features.iswap_transport import iSWAPTransport
from pylabrobot.hamilton.star.motion import attach_viewer_motion
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb_lid
from pylabrobot.resources.plate import Plate

from .demo import build_facility, star_of
from .server import Viewer3D


async def main() -> None:
  logging.disable(logging.WARNING)
  facility = build_facility()
  star = star_of(facility)
  deck = star.deck
  deck.get_resource("destination_1").unassign()
  plate = deck.get_resource("source_1")
  assert isinstance(plate, Plate) and plate.parent is not None
  lid = cor_96_wellplate_360uL_Fb_lid(name="source_1_lid")
  plate.assign_child_resource(lid)
  await star.setup()
  if star.iswap is None:
    raise RuntimeError("the simulated STARlet has no iSWAP")
  transport = iSWAPTransport(star.iswap)
  start, other = plate.parent, deck.get_resource("destination_carrier").children[1]

  viewer = Viewer3D(facility, name="transport_demo.py")
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await asyncio.sleep(1.0)

  await star.iswap.make_space()
  while True:
    print("the plate goes over, with its lid")
    await transport.move_resource(plate, other, pickup_direction="front")
    print("its lid comes off, onto the site it left")
    await transport.move_resource(lid, start, pickup_direction="front")
    print("and goes back on")
    await transport.move_resource(lid, plate, pickup_direction="front")
    print("the plate comes back, gripped from the back: turned round")
    await transport.move_resource(plate, start, pickup_direction="back", drop_direction="front")
    print("and is turned back")
    await transport.move_resource(plate, start, pickup_direction="front", drop_direction="back")
    await asyncio.sleep(1.0)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
