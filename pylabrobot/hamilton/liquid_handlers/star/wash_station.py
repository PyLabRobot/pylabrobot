"""STARWashStation: wash/pump station control for Hamilton STAR liquid handlers."""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)


class STARWashStation:
  """Controls a wash / pump station on a Hamilton STAR.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for the dual-chamber pump station subsystem and delegates
  I/O to the driver.
  """

  def __init__(self, driver: "STARDriver"):
    self._driver = driver

  class Type(enum.IntEnum):
    """Pump station type enumeration."""
    CORE_96_SINGLE = 0
    DC_SINGLE_REV_02 = 1
    RERERE_SINGLE = 2
    CORE_96_DUAL = 3
    DC_DUAL = 4
    RERERE_DUAL = 5

  async def request_settings(self, station: int = 1) -> "Type":
    """Query pump station type (C0:ET).

    Args:
      station: pump station number (1..3).

    Returns:
      Pump station type code:
        0 = CoRe 96 wash station (single chamber)
        1 = DC wash station (single chamber rev 02)
        2 = ReReRe (single chamber)
        3 = CoRe 96 wash station (dual chamber)
        4 = DC wash station (dual chamber)
        5 = ReReRe (dual chamber)
    """

    assert 1 <= station <= 3, "station must be between 1 and 3"

    resp = await self._driver.send_command(module="C0", command="ET", fmt="et#", ep=station)
    return STARWashStation.Type(resp["et"])

  async def initialize_valves(self, station: int = 1):
    """Initialize pump station valves — dual chamber only (C0:EJ).

    Args:
      station: pump station number (1..3).
    """

    assert 1 <= station <= 3, "station must be between 1 and 3"

    return await self._driver.send_command(module="C0", command="EJ", ep=station)

  async def fill_chamber(
    self,
    station: int = 1,
    drain_before_refill: bool = False,
    wash_fluid: int = 1,
    chamber: int = 2,
    waste_chamber_suck_time_after_sensor_change: int = 0,
  ):
    """Fill selected dual chamber (C0:EH).

    The wash fluid / chamber combination is encoded as a connection index:
      0 = wash fluid 1 <-> chamber 2
      1 = wash fluid 1 <-> chamber 1
      2 = wash fluid 2 <-> chamber 1
      3 = wash fluid 2 <-> chamber 2

    Args:
      station: pump station number (1..3).
      drain_before_refill: drain chamber before refill.
      wash_fluid: wash fluid selector (1 or 2).
      chamber: chamber selector (1 or 2).
      waste_chamber_suck_time_after_sensor_change: suck time in seconds after sensor
        change (for error handling only).
    """

    assert 1 <= station <= 3, "station must be between 1 and 3"
    assert 1 <= wash_fluid <= 2, "wash_fluid must be between 1 and 2"
    assert 1 <= chamber <= 2, "chamber must be between 1 and 2"

    # wash fluid <-> chamber connection
    connection = {(1, 2): 0, (1, 1): 1, (2, 1): 2, (2, 2): 3}[wash_fluid, chamber]

    return await self._driver.send_command(
      module="C0",
      command="EH",
      ep=station,
      ed=drain_before_refill,
      ek=connection,
      eu=f"{waste_chamber_suck_time_after_sensor_change:02}",
      wait=False,
    )

  async def drain(self, station: int = 1):
    """Drain dual chamber system (C0:EL).

    Args:
      station: pump station number (1..3).
    """

    assert 1 <= station <= 3, "station must be between 1 and 3"

    return await self._driver.send_command(module="C0", command="EL", ep=station)
