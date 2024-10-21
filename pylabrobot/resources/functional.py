import json
import logging
import os
import random
from collections import deque
from typing import AsyncGenerator, Deque, List, Optional
from pylabrobot.resources.tip_rack import TipRack, TipSpot


logger = logging.getLogger("pylabrobot.resources")


def get_all_tip_spots(tip_racks: List[TipRack]) -> List[TipSpot]:
  return [spot for rack in tip_racks for spot in rack.get_all_items()]


async def linear_tip_spot_generator(
  tip_spots: List[TipSpot],
  cache_file_path: Optional[str]=None,
  repeat: bool=False
) -> AsyncGenerator[TipSpot, None]:
  """ Tip spot generator with disk caching. Linearly iterate through all tip spots and
  raise StopIteration when all spots have been used. """
  tip_spot_idx = 0
  if cache_file_path is not None and os.path.exists(cache_file_path):
    with open(cache_file_path, "r", encoding="utf-8") as f:
      data = json.load(f)
      tip_spot_idx = data["tip_spot_idx"]
      logger.info("loaded tip idx from disk: %s", data)

  while True:
    if cache_file_path is not None:
      with open(cache_file_path, "w", encoding="utf-8") as f:
        json.dump({"tip_spot_idx": tip_spot_idx}, f)
    yield tip_spots[tip_spot_idx]
    tip_spot_idx += 1
    if tip_spot_idx >= len(tip_spots):
      if repeat:
        tip_spot_idx = 0
      else:
        return


async def randomized_tip_spot_generator(
  tip_spots: List[TipSpot],
  K: int, cache_file_path: Optional[str]=None
) -> AsyncGenerator[TipSpot, None]:
  """ Randomized tip spot generator with disk caching. Don't return tip spots that have been
  sampled in the last K samples. """

  recently_sampled: Deque[str] = deque(maxlen=K)

  if cache_file_path is not None and os.path.exists(cache_file_path):
    with open(cache_file_path, "r", encoding="utf-8") as f:
      data = json.load(f)
      recently_sampled = deque(data["recently_sampled"], maxlen=K)
      logger.info("loaded recently sampled tip spots from disk: %s", recently_sampled)

  while True:
    available_tips = [ts for ts in tip_spots if ts.name not in recently_sampled]

    if not available_tips:
      raise RuntimeError("All tips have been used recently, resetting list.")

    chosen_tip_spot = random.choice(available_tips)
    recently_sampled.append(chosen_tip_spot.name)

    if cache_file_path is not None:
      with open(cache_file_path, "w", encoding="utf-8") as f:
        json.dump({"recently_sampled": list(recently_sampled)}, f)

    yield chosen_tip_spot
