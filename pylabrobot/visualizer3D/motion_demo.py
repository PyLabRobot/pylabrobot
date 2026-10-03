"""A simulated STARlet pipetting, with the drives' motion acted out between the commands.

Run it:

    python -m pylabrobot.visualizer3D.motion_demo

The viewer reads each firmware command for its targets and the page plays how the drives get
there: across (the arm and the channels at once), down, tips taken or left at the bottom, back up.
Each command waits until the page has played it, so the run goes at the pace of the picture. Add
`?motion=2` to the address (before the `#`) for twice the speed, `?motion=0` to skip the motion.

Column by column: pick up eight tips, aspirate from the source plate, put the tips back.
"""

import asyncio
import logging

from pylabrobot.hamilton.star.motion import attach_viewer_motion
from pylabrobot.resources import set_volume_tracking
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import set_tip_tracking

from .demo import build_facility, fill, star_of
from .server import Viewer3D

ROWS = "ABCDEFGH"


async def main() -> None:
  logging.disable(logging.WARNING)
  set_volume_tracking(True)
  # So a tip picked up is the one that was in the spot, carried to the channel and back.
  set_tip_tracking(True)

  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  pipettes = star.pipettes
  if pipettes is None:
    raise RuntimeError("the simulated STARlet has no channels")

  rack = star.deck.get_resource("tips_0")
  source = star.deck.get_resource("source_0")
  if not isinstance(rack, TipRack) or not isinstance(source, Plate):
    raise TypeError("the demo deck no longer holds the rack and plate this run uses")
  fill(source, 300.0)

  viewer = Viewer3D(facility, name="motion_demo.py")
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await asyncio.sleep(1.0)

  for column in range(1, 13):
    tips = [rack.get_item(f"{row}{column}") for row in ROWS]
    wells = [source.get_item(f"{row}{column}") for row in ROWS]
    print(f"column {column}: pick up, aspirate, put back")
    await pipettes.pick_up_tips(tips)
    await pipettes.aspirate(wells, [50.0] * len(wells))
    await pipettes.drop_tips(tips)

  print("run complete; the viewer stays up")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
