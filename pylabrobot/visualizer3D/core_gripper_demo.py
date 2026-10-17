"""The CO-RE gripper: tools on, a plate gripped off its site, carried across the deck, put down.

The plate hangs from the front tool's jaws in the model, rides the X-arm, and passes to the site
when the jaws open.

Run it:

    python -m pylabrobot.visualizer3D.core_gripper_demo
"""

import asyncio
import logging

from pylabrobot.resources.plate import Plate

from ..hamilton.star.motion import attach_viewer_motion
from .demo import build_facility, star_of
from .server import Viewer3D


async def main() -> None:
  logging.disable(logging.WARNING)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  grippers = star.core_grippers
  if grippers is None:
    raise RuntimeError("the simulated STARlet has no CO-RE grippers")

  deck = star.deck
  source = deck.get_resource("source_0")
  if not isinstance(source, Plate):
    raise RuntimeError("the demo deck no longer holds the plate this run grips")

  # The site it goes to has to be free: the destination carrier's front-most site stands empty.
  destination_carrier = deck.get_resource("destination_carrier")
  site = min(destination_carrier.children, key=lambda s: s.get_absolute_location().y)
  held = [child for child in site.children if isinstance(child, Plate)]
  for plate in held:
    plate.unassign()

  viewer = Viewer3D(facility, name="core_gripper_demo.py", open_browser=False)
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await viewer.wait_for_models()
  print("the page is up; the run starts in ten seconds")
  await asyncio.sleep(10.0)

  print("picking up the CO-RE grip tools")
  await grippers.pick_up_tools(front_channel=7)

  print(f"gripping {source.name} where it stands")
  await grippers.pick_up_resource(source)

  here = source.get_absolute_location().x
  there = site.get_absolute_location().x
  print(f"carrying it across the deck, {there - here:.0f} mm to the front-most destination site")
  x = await star.x_arm.request_position()
  await star.x_arm.move_to_x_position(x + (there - here))

  print("putting it down on the site")
  await grippers.drop_resource(site)

  print("returning the tools to their holder")
  await grippers.drop_tools()

  print("run complete; the viewer stays up")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
