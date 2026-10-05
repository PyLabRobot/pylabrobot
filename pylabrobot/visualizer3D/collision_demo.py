"""Opening the iSWAP's jaws wider than the space beside a gripped plate allows.

Run it:

    python -m pylabrobot.visualizer3D.collision_demo

The plate is gripped the long way - its steps are clean, and they play as they normally would -
and then the jaws are opened the rest of the way to the drive's own limit, as an open-everything
command does. The check walks the fingers' way finely, finds where they first meet the tip racks
standing taller beside the plate, and the jaws are driven there and stopped: what the wide open
would hit is drawn over the scene, with the joints left where the meeting happened.

With the viewer's `raise_on_collision` on and its collision gate attached, the arm's checks refuse
what they find; this demo shows the finding itself, driven to rather than refused at.
"""

import asyncio
import logging
from typing import Dict, List

from pylabrobot.hamilton.star.driver.features.iswap_collisions import judge
from pylabrobot.hamilton.star.driver.features.iswap_transport import (
  Grip,
  Open,
  Plan,
  iSWAPTransport,
)
from pylabrobot.hamilton.star.motion import attach_viewer_collisions, attach_viewer_motion
from pylabrobot.resources.collision import Collision, Group

from .demo import build_facility, star_of
from .server import Viewer3D


async def drive_to_meeting(
  transport: iSWAPTransport, step: Open, groups: Dict[str, Group], found: List[Collision]
) -> None:
  """Drive the open with its target at the meeting instant.

  The check walked the way finely and says how far into it the first meeting was (`when`); the
  jaws widen linearly, so the width there is the width they stand at carried that far.
  """
  iswap = transport.iswap
  moment = min(collision.when for collision in found if collision.when is not None)
  if isinstance(step, Open):
    here = float(iswap.gripper.jaw_width)
    await iswap.gripper_move_to_jaw_position(
      round(here + (step.width - here) * moment, 1),
      speed=step.speed,
      acceleration=step.acceleration,
    )
  else:
    raise RuntimeError(
      f"a meeting part way through a {type(step).__name__} is not driven to; the step is refused "
      "where it stands"
    )


async def main() -> None:
  logging.disable(logging.WARNING)
  facility = build_facility()
  star = star_of(facility)
  deck = star.deck
  # The site across the aisle stays empty, so what stands beside the gripped plate is the taller
  # tip racks: what an over-wide open meets here first.
  deck.get_resource("destination_1").unassign()
  await star.setup()

  iswap = star.iswap
  if iswap is None:
    raise RuntimeError("the simulated STARlet has no iSWAP")
  viewer = Viewer3D(facility, name="collision_demo.py", open_browser=False, raise_on_collision=True)
  await viewer.start()
  attach_viewer_motion(star.driver, viewer)
  transport = iSWAPTransport(iswap, check_collisions=False)
  attach_viewer_collisions(star.driver, viewer, transport)
  print("waiting for a browser to draw the scene")
  await viewer.wait_for_browser()
  await viewer.wait_for_models()
  print("the page is up; the run starts in ten seconds")
  await asyncio.sleep(10.0)

  await iswap.make_space()
  print("the arm is cleared; gripping the plate the long way")

  plate = deck.get_resource("source_1")
  plan = transport.plan_pick_up(plate, direction="front")
  for k, step in enumerate(plan.steps):
    found, _ = judge(transport, Plan(steps=[step], reach=plan.reach, facing=plan.facing))
    if found:
      raise RuntimeError(f"the pick-up was expected to be clean, and step {k} was not: {found}")
    await transport._do(step, plan)
    if isinstance(step, Grip):
      break
  gripper = iswap.gripper
  assert gripper is not None
  over_wide = float(gripper.jaw_range[1])
  print(
    f"the plate is gripped at its site, jaws at {gripper.jaw_width:.1f} mm; "
    f"opening the rest of the way to the drive's limit, {over_wide:.1f} mm"
  )

  over = Open(width=over_wide)
  found, groups = judge(transport, Plan(steps=[over], reach=plan.reach, facing=plan.facing))
  if found:
    await drive_to_meeting(transport, over, groups, found)
    await viewer.show_collisions(found)
    hit = ", ".join(sorted({collision.obstacle.name for collision in found}))
    print(f"stopped part way through the open: the fingers met {hit}")
    for collision in found:
      print(f"  {collision}")
  else:
    print("the wide open was clean after all")
  print("the run is paused where it met; ctrl-c ends the demo")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
