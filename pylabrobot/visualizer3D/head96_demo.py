"""The 96 head: a full plate of tips, a full plate of aspiration, a full plate of dispensing.

The head moves as one: down to the liquid's surface, following it as it draws, then out and over.

Run it:

    python -m pylabrobot.visualizer3D.head96_demo
"""

import asyncio
import logging

from pylabrobot.resources import set_volume_tracking
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import set_tip_tracking

from ..hamilton.star.motion import attach_viewer_motion
from .demo import build_facility, fill, star_of
from .server import Viewer3D


async def main() -> None:
  logging.disable(logging.WARNING)
  set_volume_tracking(True)
  set_tip_tracking(True)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  head96 = star.head96
  if head96 is None:
    raise RuntimeError("the simulated STARlet has no 96 head")

  deck = star.deck
  rack = deck.get_resource("tips_0")
  if not isinstance(rack, TipRack):
    raise RuntimeError("the demo deck no longer holds the tips this run uses")
  source = deck.get_resource("source_0")
  destination = deck.get_resource("destination_0")
  if not isinstance(source, Plate) or not isinstance(destination, Plate):
    raise RuntimeError("the demo deck no longer holds the plates this run uses")
  fill(source, 200.0)

  viewer = Viewer3D(facility, name="head96_demo.py", open_browser=False)
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await viewer.wait_for_models()
  print("the page is up; the run starts in ten seconds")
  await asyncio.sleep(10.0)

  print("picking up ninety-six tips")
  await head96.pick_up_tips(rack)

  print("aspirating fifty uL from every well of the source plate")
  await head96.aspirate(source, 50.0)

  print("dispensing into the destination plate")
  await head96.dispense(destination, 50.0)

  print("run complete; the viewer stays up")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
