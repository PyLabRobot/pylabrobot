import json
import logging
import os
import random
from collections import deque
from typing import AsyncGenerator, List, Optional
from pylabrobot.resources.tip_rack import TipRack, TipSpot


logger = logging.getLogger("pylabrobot.resources")

async def linear_tip_spot_generator(
  tip_racks: List[TipRack],
  cache_file_path: Optional[str]=None
) -> AsyncGenerator[TipSpot, None]:
  """ Tip spot generator with disk caching. Linearly iterate through all tip spots and
  raise StopIteration when all spots have been used. """
  tip_rack_idx, tip_spot_idx = 0, 0
  if cache_file_path is not None and os.path.exists(cache_file_path):
    with open(cache_file_path, "r", encoding="utf-8") as f:
      data = json.load(f)
      tip_rack_idx, tip_spot_idx = data["tip_rack_index"], data["tip_spot_idx"]
      logger.info.f("loaded tip idx from disk: %s", data)

  while tip_rack_idx < len(tip_racks):
    while tip_spot_idx < 96:
      tip_spot_idx += 1
      if cache_file_path is not None:
        with open(cache_file_path, "w", encoding="utf-8") as f:
          json.dump({"tip_rack_index": tip_rack_idx, "tip_spot_idx": tip_spot_idx}, f)
      yield tip_racks[tip_rack_idx][tip_spot_idx]

    tip_rack_idx += 1
    tip_spot_idx = 0


async def randomized_tip_spot_generator(
  tip_racks: List[TipRack], K: int,
  cache_file_path: Optional[str]=None
) -> AsyncGenerator[TipSpot, None]:
  """ Randomized tip spot generator with disk caching. Don't return tip spots that have been
  sampled in the last K samples. """
  
  all_tip_spots = [rack.get_item(spot_idx) for rack in tip_racks for spot_idx in range(rack.num_items)]
  recently_sampled = deque(maxlen=K)

  if cache_file_path is not None and os.path.exists(cache_file_path):
    with open(cache_file_path, "r", encoding="utf-8") as f:
      data = json.load(f)
      recently_sampled = deque(data["recently_sampled"], maxlen=K)
      logger.info.f("loaded recently sampled tip spots from disk: %s", recently_sampled)

  while True:
    available_tips = [ts for ts in all_tip_spots if ts.name not in recently_sampled]

    if not available_tips:
      raise RuntimeError("All tips have been used recently, resetting list.")

    chosen_tip_spot = random.choice(available_tips)
    recently_sampled.append(chosen_tip_spot.name)

    if cache_file_path is not None:
      with open(cache_file_path, "w", encoding="utf-8") as f:
        json.dump({"recently_sampled": list(recently_sampled)}, f)

    yield chosen_tip_spot
