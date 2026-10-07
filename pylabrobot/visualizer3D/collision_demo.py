"""A grip with no room beside the plate: the whole method is judged, then played to the meeting.

Run it:

    python -m pylabrobot.visualizer3D.collision_demo

The tip carrier stands flush against the source carrier, as a packed deck puts them. The iSWAP's
pick-up is judged before anything moves - the whole method, one way from where the arm stands - and
the fingers meet the carrier's outer wall on the way down to the plate, at the opening width the
grip itself uses. The clean steps then play as they would, and the descent is driven part way, to
the instant of the meeting: the arm stands where the fingers would have hit, with what they met
drawn over the scene.
"""

import asyncio
import logging

from pylabrobot.hamilton.star.driver.features.iswap_collisions import judge
from pylabrobot.hamilton.star.driver.features.iswap_transport import (
  Rise,
  iSWAPTransport,
)
from pylabrobot.hamilton.star.driver.features.star_collisions import CollisionError
from pylabrobot.hamilton.star.motion import attach_viewer_collisions, attach_viewer_motion

from .demo import build_facility, star_of
from .server import Viewer3D


async def main() -> None:
  logging.disable(logging.WARNING)
  facility = build_facility()
  star = star_of(facility)
  deck = star.deck
  deck.get_resource("destination_1").unassign()
  # The carriers stand flush, as a packed deck puts them: the tip carrier's outer wall 1 mm from
  # the source carrier, and no room for the fingers beside the plate they grip.
  source_carrier = deck.get_resource("source_carrier")
  deck.unassign_child_resource(source_carrier)
  deck.assign_child_resource(source_carrier, track=7)
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
  print("the page is up; the run starts in fifteen seconds")
  await asyncio.sleep(15.0)

  await iswap.make_space()
  print("the arm is cleared; the pick-up is judged before anything moves")

  plate = deck.get_resource("source_1")
  plan = transport.plan_pick_up(plate, direction="front")
  found, _ = judge(transport, plan)
  if not found:
    print("the pick-up was clean after all; it plays as it normally would")
    for step in plan.steps:
      await transport._do(step, plan)
    print("the run is paused where it ended; ctrl-c ends the demo")
    await asyncio.Event().wait()

  # The way is acted out to the instant of the meeting: the clean steps play as they would, and the
  # step the meeting falls in is driven part way there. The checks refuse the last of it - what
  # they found is drawn, and the arm stands where the fingers would have met.
  met = [c for c in found if c.when is not None]
  first = min(met, key=lambda c: c.when if c.when is not None else float("inf"))
  when = first.when
  assert when is not None
  step_index = int(when)
  step = plan.steps[step_index]
  moment = when - step_index
  for earlier in plan.steps[:step_index]:
    await transport._do(earlier, plan)
  if not isinstance(step, Rise):
    raise RuntimeError(f"acting out a meeting inside a {type(step).__name__} is not done here")
  stood_at = max(s.z for s in plan.steps[:step_index] if isinstance(s, Rise))
  here = await iswap.elbow_request_z_position()
  try:
    await iswap.elbow_move_to_z_position(
      round(here + (step.z - stood_at) * moment, 1),
      speed=step.speed,
      acceleration=step.acceleration,
    )
  except CollisionError:
    pass  # the gate drew what it found and refused the last of the way
  await viewer.show_collisions(found)
  hit = ", ".join(sorted({collision.obstacle.name for collision in found}))
  print(f"the way is held {moment * 100:.0f}% into the descent: the fingers met {hit}")
  for collision in found:
    print(f"  {collision}")
  print("the run is paused where it would have met; ctrl-c ends the demo")
  await asyncio.Event().wait()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
