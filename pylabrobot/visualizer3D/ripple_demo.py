"""The Y ripple: one aspirate, five plates, the channels fanning out in Y.

A1 of every plate on the source carrier is aspirated in one go - the channels start their Y moves
one after another (the ripple), each ending over a different plate. What was drawn is then
dispensed into the first column of the front-most plate on the destination carrier: the channels
close back up to the 9 mm pitch, dispensing down the column.

Run it:

    python -m pylabrobot.visualizer3D.ripple_demo
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

ROWS = "ABCDE"  # the carrier holds five plates, so five channels carry the aspirate


async def main() -> None:
  logging.disable(logging.WARNING)
  set_volume_tracking(True)
  set_tip_tracking(True)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  pipettes = star.pipettes
  if pipettes is None:
    raise RuntimeError("the simulated STARlet has no channels")

  deck = star.deck
  source_carrier = deck.get_resource("source_carrier")
  destination_carrier = deck.get_resource("destination_carrier")
  rack = deck.get_resource("tips_0")
  if not isinstance(rack, TipRack):
    raise RuntimeError("the demo deck no longer holds the tips this run uses")

  plates = [
    child for site in source_carrier.children for child in site.children if isinstance(child, Plate)
  ]
  # Channel 0 is the back-most channel, and per-channel Y runs back to front: the wells go to the
  # channels in the same order, so every plate is hit at once, in a single command.
  plates.sort(key=lambda p: p.get_absolute_location().y, reverse=True)
  if len(plates) < 2:
    raise RuntimeError("expected several plates on the source carrier")
  for plate in plates:
    fill(plate, 200.0)

  destinations = [
    child
    for site in destination_carrier.children
    for child in site.children
    if isinstance(child, Plate)
  ]
  front = min(destinations, key=lambda p: p.get_absolute_location().y)

  viewer = Viewer3D(facility, name="ripple_demo.py", open_browser=False)
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await viewer.wait_for_models()
  print("the page is up; the run starts in ten seconds")
  await asyncio.sleep(10.0)

  print(f"picking up {len(ROWS)} tips, one per channel that will aspirate")
  await pipettes.pick_up_tips([rack.get_item(f"{row}1") for row in ROWS])

  wells = [plate.get_item("A1") for plate in plates]
  print(f"aspirating A1 of {len(wells)} plates at once - the channels ripple out in Y")
  await pipettes.aspirate(wells, [50.0] * len(wells))

  column = [front.get_item(f"{row}1") for row in ROWS]
  print(f"dispensing into the first column of {front.name}, the front-most destination plate")
  await pipettes.dispense(column, [50.0] * len(column))

  print("run complete; the viewer stays up")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
