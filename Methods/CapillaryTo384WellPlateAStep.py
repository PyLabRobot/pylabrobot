"""Step through CapillaryTo384WellPlateA one move at a time.

Same motion as ``CapillaryTo384WellPlateA.py``, but waits for a key before
each move so you can watch the arm like the Peak pendant.

  Space or Enter  — do the next move
  q               — quit (arm still shuts down cleanly)

Usage::

    python Methods/CapillaryTo384WellPlateAStep.py
"""

from __future__ import annotations

import asyncio

try:
  from Methods.CapillaryTo384WellPlateA import (  # type: ignore[import-not-found]
    run_capillary_to_384_well,
  )
except ImportError:
  from CapillaryTo384WellPlateA import run_capillary_to_384_well  # type: ignore[no-redef]


def main() -> None:
  asyncio.run(run_capillary_to_384_well(step=True))


if __name__ == "__main__":
  main()
