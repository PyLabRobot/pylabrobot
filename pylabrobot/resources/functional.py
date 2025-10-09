import atexit
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


class linear_tip_spot_generator:
  def __init__(
    self, tip_spots: List[TipSpot], cache_file_path: Optional[str] = None, repeat: bool = False
  ):
    self.tip_spots = tip_spots
    self.cache_file_path = cache_file_path
    self.repeat = repeat
    self._tip_spot_idx = 0

    if self.cache_file_path and os.path.exists(self.cache_file_path):
      try:
        with open(self.cache_file_path, "r", encoding="utf-8") as f:
          data = json.load(f)
          self._tip_spot_idx = data.get("tip_spot_idx", 0)
          logger.info("Loaded tip idx from disk: %s", data)
      except Exception as e:
        logger.error("Failed to load cache file: %s", e)

    atexit.register(self.save_state)

  def __aiter__(self):
    return self

  async def __anext__(self) -> TipSpot:
    while True:
      self.save_state()
      if self._tip_spot_idx >= len(self.tip_spots):
        if self.repeat:
          self._tip_spot_idx = 0
        else:
          raise StopAsyncIteration

      self._tip_spot_idx += 1
      return self.tip_spots[self._tip_spot_idx - 1]

  def save_state(self):
    if self.cache_file_path:
      try:
        with open(self.cache_file_path, "w", encoding="utf-8") as f:
          json.dump({"tip_spot_idx": self._tip_spot_idx}, f)
          logger.info("Saved tip idx to disk: %s", self._tip_spot_idx)
      except Exception as e:
        logger.error("Failed to save cache file: %s", e)

  def set_index(self, index: int):
    self._tip_spot_idx = index

  def get_num_tips_left(self) -> int:
    """Returns the number of tips left to be sampled. Raises an error if repeat is True."""
    if self.repeat:
      raise RuntimeError("Cannot get number of tips left when repeat is True.")
    return len(self.tip_spots) - self._tip_spot_idx

  async def get(self, n: int) -> List[TipSpot]:
    """Get the next n tip spots."""
    assert 0 <= n <= self.get_num_tips_left()
    return [await self.__anext__() for _ in range(n)]


class randomized_tip_spot_generator:
  def __init__(
    self,
    tip_spots: List[TipSpot],
    K: int,
    cache_file_path: Optional[str] = None,
  ):
    """Randomized tip spot generator with disk caching. Don't return tip spots that have been
    sampled in the last K samples."""

    self.tip_spots = tip_spots
    self.cache_file_path = cache_file_path

    self.recently_sampled: Deque[str] = deque(maxlen=K)

    if self.cache_file_path is not None and os.path.exists(self.cache_file_path):
      with open(self.cache_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        self.recently_sampled = deque(data["recently_sampled"], maxlen=K)
        logger.info(
          "loaded recently sampled tip spots from disk: %s",
          self.recently_sampled,
        )

    atexit.register(self.save_state)

  def save_state(self):
    if self.cache_file_path is not None:
      with open(self.cache_file_path, "w", encoding="utf-8") as f:
        json.dump({"recently_sampled": list(self.recently_sampled)}, f)

  async def __anext__(self) -> TipSpot:
    while True:
      available_tips = [ts for ts in self.tip_spots if ts.name not in self.recently_sampled]

      if not available_tips:
        raise RuntimeError("All tips have been used recently. No tips available.")

      chosen_tip_spot = random.choice(available_tips)
      self.recently_sampled.append(chosen_tip_spot.name)

      self.save_state()

      return chosen_tip_spot

  def __aiter__(self):
    return self

  async def get(self, n: int) -> List[TipSpot]:
    """Get the next n tip spots."""
    assert 0 <= n
    return [await self.__anext__() for _ in range(n)]
