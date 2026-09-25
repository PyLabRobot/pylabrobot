"""A STARlet drawn from the model files it ships with, rather than as boxes.

Run it:

    python -m pylabrobot.visualizer3D.model_demo

No path is declared and no models root is passed: a resource names its model, a `.glb` under
the package is named after it, and the viewer draws it in the resource's own frame, in metres,
Z up. What is drawn from a file and what stays a box is printed on startup. The iSWAP turns and
its jaws work until stopped with Ctrl-C.
"""

import asyncio
import logging
from typing import Any, Dict, List, Set, Tuple

from .demo import build_facility, star_of, turn_the_arm, work_the_jaws
from .server import Viewer3D


def what_is_drawn(models: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str]]:
  """The scene's models, sorted, as three lists: drawn from a file, named without one, unnamed.

  Read off the models as the viewer serves them: one with a `mesh` is drawn from a file. An
  unnamed model is listed by type and category.
  """
  drawn: Set[str] = set()
  missing: Set[str] = set()
  unnamed: Set[str] = set()
  for model in models:
    name = str(model.get("model") or "")
    if not name:
      unnamed.add(f"{model.get('type')} ({model.get('category')})")
    elif "mesh" in model:
      drawn.add(name)
    else:
      missing.add(name)
  return sorted(drawn), sorted(missing), sorted(unnamed)


def report(models: List[Dict[str, Any]]) -> None:
  """Print what the viewer draws from a file and what it does not."""
  drawn, missing, unnamed = what_is_drawn(models)
  print(f"\n  drawn from a model file ({len(drawn)})")
  for model in drawn:
    print(f"    {model}")
  print(f"\n  named, but no file is under that name ({len(missing)})")
  for model in missing:
    print(f"    {model}")
  print(f"\n  no model name, so nothing to look up ({len(unnamed)})")
  for kind in unnamed:
    print(f"    {kind}")
  print()


async def main() -> None:
  logging.disable(logging.WARNING)
  facility = build_facility(bare=True)
  star = star_of(facility)
  await star.setup()

  viewer = Viewer3D(facility, name="model_demo.py")
  await viewer.start()
  # The scene as the first client will be handed it, its meshes resolved.
  report(viewer._scene_message()["models"])
  if star.iswap is None:
    print("  this STARlet has no iSWAP, so nothing moves")
    return
  # The arm turns and the jaws work at the same time, as they do on the device: one drive does not
  # wait on the other.
  await asyncio.gather(turn_the_arm(star), work_the_jaws(star))


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
