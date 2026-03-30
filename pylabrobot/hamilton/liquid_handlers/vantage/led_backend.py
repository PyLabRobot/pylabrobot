"""Vantage LED backend: translates LED operations into Vantage firmware commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.led_control.backend import LEDBackend

if TYPE_CHECKING:
  from .driver import VantageDriver


@dataclass
class VantageLEDParams(BackendParams):
  """Vantage-specific LED parameters.

  Args:
    uv: UV channel intensity 0-100.
    blink_interval: Blink interval in ms. Only used when mode is "blink".
  """

  uv: int = 0
  blink_interval: Optional[int] = None


class VantageLEDBackend(LEDBackend):
  """Encodes LED commands for the Vantage master module (C0AM)."""

  def __init__(self, driver: VantageDriver):
    self._driver = driver

  async def set_color(
    self,
    mode: str,
    intensity: int,
    white: int,
    red: int,
    green: int,
    blue: int,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, VantageLEDParams):
      backend_params = VantageLEDParams()

    uv = backend_params.uv
    blink_interval = backend_params.blink_interval

    mode_to_li = {"on": 1, "off": 0, "blink": 2}
    if mode not in mode_to_li:
      raise ValueError(f"Invalid mode {mode!r}. Expected 'on', 'off', or 'blink'.")
    if blink_interval is not None and mode != "blink":
      raise ValueError("blink_interval is only used when mode is 'blink'.")

    await self._driver.send_command(
      module="C0AM",
      command="LI",
      li=mode_to_li[mode],
      os=intensity,
      ok=blink_interval or 750,
      ol=f"{white} {red} {green} {blue} {uv}",
    )

  async def turn_off(self) -> None:
    await self.set_color(mode="off", intensity=0, white=0, red=0, green=0, blue=0)
