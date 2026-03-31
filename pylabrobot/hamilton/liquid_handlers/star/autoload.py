"""STARAutoload: autoload module control for Hamilton STAR liquid handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

from pylabrobot.resources.barcode import Barcode, Barcode1DSymbology

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)


class STARAutoload:
  """Controls the autoload module on a Hamilton STAR.

  This is a plain helper class (not a CapabilityBackend). It encapsulates the
  firmware protocol for the autoload subsystem and delegates I/O to the driver.

  Methods that the legacy backend called with ``Carrier`` objects now take
  ``carrier_end_rail: int`` — the caller is responsible for computing the rail
  from carrier geometry.
  """

  # 1D barcode symbology bitmask
  # Each symbology corresponds to exactly one bit in the 8-bit barcode type field.
  # Bit definitions from spec:
  #   Bit 0 = ISBT Standard
  #   Bit 1 = Code 128 (Subset B and C)
  #   Bit 2 = Code 39
  #   Bit 3 = Codabar
  #   Bit 4 = Code 2of5 Interleaved
  #   Bit 5 = UPC A/E
  #   Bit 6 = YESN/EAN 8
  #   Bit 7 = (unused / undocumented)

  barcode_1d_symbology_dict: Dict[Barcode1DSymbology, str] = {
    "ISBT Standard": "01",  # bit 0
    "Code 128 (Subset B and C)": "02",  # bit 1
    "Code 39": "04",  # bit 2
    "Codebar": "08",  # bit 3
    "Code 2of5 Interleaved": "10",  # bit 4
    "UPC A/E": "20",  # bit 5
    "YESN/EAN 8": "40",  # bit 6
    "ANY 1D": "7F",  # bits 0-6
  }

  def __init__(self, driver: "STARDriver", instrument_size_slots: int = 54):
    self.driver = driver
    self._instrument_size_slots = instrument_size_slots
    self._default_1d_symbology: Barcode1DSymbology = "Code 128 (Subset B and C)"

  # -- lifecycle -------------------------------------------------------------

  async def _on_setup(self):
    """Initialize Auto load module (C0:II)."""
    await self.driver.send_command(module="C0", command="II")

  async def _on_stop(self):
    pass

  async def request_initialization_status(self) -> bool:
    """Request autoload initialization status (I0:QW)."""
    resp = await self.driver.send_command(module="I0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  # -- z-position safety -----------------------------------------------------

  async def move_to_safe_z_position(self):
    """Move autoload carrier handling wheel to safe Z position (C0:IV)."""
    return await self.driver.send_command(module="C0", command="IV")

  # -- position queries ------------------------------------------------------

  async def request_track(self) -> int:
    """Request current track of the autoload carrier handler (C0:QA).

    Returns:
      track (0..54)
    """
    resp = await self.driver.send_command(module="C0", command="QA", fmt="qa##")
    return int(resp["qa"])

  async def request_type(self) -> str:
    """Query the autoload module type (C0:CQ).

    Returns:
        Human-readable autoload module type string, or the raw code if unknown.
    """

    autoload_type_dict = {
      0: "ML-STAR with 1D Barcode Scanner",
      1: "XRP Lite",
      2: "ML-STAR with 2D Barcode Scanner",
    }

    resp = await self.driver.send_command(module="C0", command="CQ", fmt="cq#")
    resp = autoload_type_dict[resp["cq"]] if resp["cq"] in autoload_type_dict else resp["cq"]

    return str(resp)

  # -- carrier sensing -------------------------------------------------------

  @staticmethod
  def _decode_hex_bitmask_to_track_list(mask_hex: str) -> List[int]:
    """Decode a hex occupancy bitmask of arbitrary length.

    Each hex nibble = 4 slots. Slot numbering starts at 1 from the rightmost nibble (LSB).
    """
    mask_hex = mask_hex.strip()

    if not all(c in "0123456789abcdefABCDEF" for c in mask_hex):
      raise ValueError(f"Invalid hex in mask: {mask_hex!r}")

    slots: List[int] = []
    bit_index = 1

    for nibble in reversed(mask_hex):
      val = int(nibble, 16)
      for bit in range(4):
        if val & (1 << bit):
          slots.append(bit_index)
        bit_index += 1

    return sorted(slots)

  async def request_presence_of_carriers_on_deck(self) -> List[int]:
    """Read the deck carrier presence sensors (C0:RC).

    Returns:
        Sorted list of deck rail positions where carriers are present.
    """
    resp = await self.driver.send_command(module="C0", command="RC")

    ce_resp = resp.split("ce")[-1]

    return self._decode_hex_bitmask_to_track_list(ce_resp)

  async def request_presence_of_carriers_on_loading_tray(self) -> List[int]:
    """Scan loading tray positions for carrier presence (C0:CS).

    Returns:
        Sorted list of loading-tray positions where carriers are present.
    """
    resp = await self.driver.send_command(module="C0", command="CS")

    if "cd" not in resp:
      raise ValueError(f"CD field missing: {resp!r}")

    mask_hex = resp.split("cd", 1)[1].strip()

    return self._decode_hex_bitmask_to_track_list(mask_hex)

  async def request_presence_of_single_carrier_on_loading_tray(self, track: int) -> bool:
    """Check whether a specific loading-tray track contains a carrier (C0:CT).

    Args:
        track: The loading-tray track number to query (1-54).

    Returns:
        True if a carrier is detected at the given track; False otherwise.
    """

    if not (1 <= track <= 54):
      raise ValueError("track must be between 1 and 54")

    track_str = str(track).zfill(2)

    resp = await self.driver.send_command(
      module="C0",
      command="CT",
      fmt="ct#",
      cp=track_str,
    )
    if resp is None:
      raise RuntimeError("Expected a response from send_command for CT, got None")

    return int(resp["ct"]) == 1

  # -- movement commands -----------------------------------------------------

  async def move_to_track(self, track: int):
    """Move autoload to specific track position (I0:XP)."""

    if not (1 <= track <= 54):
      raise ValueError("track must be between 1 and 54")

    await self.move_to_safe_z_position()

    track_no_as_safe_str = str(track).zfill(2)
    return await self.driver.send_command(module="I0", command="XP", xp=track_no_as_safe_str)

  async def park(self):
    """Park autoload to max position (I0:XP)."""

    max_x_pos = str(self._instrument_size_slots).zfill(2)

    await self.move_to_safe_z_position()

    return await self.driver.send_command(module="I0", command="XP", xp=max_x_pos)

  # -- belt operations -------------------------------------------------------

  async def take_carrier_out_to_belt(self, carrier_end_rail: int):
    """Take carrier out to identification position for barcode reading (C0:CN).

    Args:
      carrier_end_rail: End rail position of the carrier on the deck.
    """

    carrier_on_loading_tray = await self.request_presence_of_single_carrier_on_loading_tray(
      carrier_end_rail
    )

    if not carrier_on_loading_tray:
      try:
        await self.driver.send_command(
          module="C0",
          command="CN",
          cp=str(carrier_end_rail).zfill(2),
        )
      except Exception as e:
        await self.move_to_safe_z_position()
        raise RuntimeError(
          f"Failed to take carrier at rail {carrier_end_rail} out to autoload belt: {e}"
        )
    else:
      raise ValueError(f"Carrier is already on the loading tray at position {carrier_end_rail}.")

  async def unload_carrier_after_barcode_scanning(self):
    """Unload carrier back to loading tray after barcode scanning (C0:CA)."""
    try:
      resp = await self.driver.send_command(
        module="C0",
        command="CA",
      )
    except Exception as e:
      await self.move_to_safe_z_position()
      raise RuntimeError(f"Failed to unload carrier after barcode scanning: {e}")

    return resp

  async def load_carrier_from_belt(
    self,
    barcode_reading: bool = False,
    barcode_reading_direction: Literal["horizontal", "vertical"] = "horizontal",
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    reading_position_of_first_barcode: float = 63.0,  # mm
    no_container_per_carrier: int = 5,
    distance_between_containers: float = 96.0,  # mm
    width_of_reading_window: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/secs
    park_autoload_after: bool = True,
  ) -> Dict[int, Optional[Barcode]]:
    """Finish loading a carrier currently on the autoload sled (C0:CL).

    Optionally reads container barcodes during the load.
    """

    if barcode_reading_direction not in ["horizontal", "vertical"]:
      raise ValueError(
        f"barcode_reading_direction must be 'horizontal' or 'vertical', "
        f"got {barcode_reading_direction!r}"
      )
    if not (0 <= reading_position_of_first_barcode <= 470):
      raise ValueError(
        f"reading_position_of_first_barcode must be between 0 and 470, "
        f"got {reading_position_of_first_barcode}"
      )
    if not (0 <= no_container_per_carrier <= 32):
      raise ValueError(
        f"no_container_per_carrier must be between 0 and 32, got {no_container_per_carrier}"
      )
    if not (0 <= distance_between_containers <= 470):
      raise ValueError(
        f"distance_between_containers must be between 0 and 470, got {distance_between_containers}"
      )
    if not (0.1 <= width_of_reading_window <= 99.9):
      raise ValueError(
        f"width_of_reading_window must be between 0.1 and 99.9, got {width_of_reading_window}"
      )
    if not (1.5 <= reading_speed <= 160.0):
      raise ValueError(f"reading_speed must be between 1.5 and 160.0, got {reading_speed}")

    barcode_reading_direction_dict = {
      "vertical": "0",
      "horizontal": "1",
    }

    if barcode_symbology is None:
      barcode_symbology = self._default_1d_symbology
    if barcode_symbology is None:
      raise RuntimeError("barcode_symbology is None after fallback to default")

    no_container_per_carrier_str = str(no_container_per_carrier).zfill(2)
    reading_position_of_first_barcode_str = str(
      round(reading_position_of_first_barcode * 10)
    ).zfill(4)
    distance_between_containers_str = str(round(distance_between_containers * 10)).zfill(4)
    width_of_reading_window_str = str(round(width_of_reading_window * 10)).zfill(3)
    reading_speed_str = str(round(reading_speed * 10)).zfill(4)

    if not barcode_reading:
      barcode_reading_direction = "vertical"  # no movement
      no_container_per_carrier_str = "00"  # no scanning

    else:
      # Choose barcode symbology
      await self.set_1d_barcode_type(barcode_symbology=barcode_symbology)

      self._default_1d_symbology = barcode_symbology

    try:
      resp = await self.driver.send_command(
        module="C0",
        command="CL",
        bd=barcode_reading_direction_dict[barcode_reading_direction],
        bp=reading_position_of_first_barcode_str,
        cn=no_container_per_carrier_str,
        co=distance_between_containers_str,
        cf=width_of_reading_window_str,
        cv=reading_speed_str,
      )
    except Exception as e:
      await self.move_to_safe_z_position()
      raise RuntimeError(f"Failed to load carrier from autoload belt: {e}")

    if park_autoload_after:
      await self.park()

    if not isinstance(resp, str):
      raise RuntimeError(f"Expected a string response from CL command, got {resp!r}")

    barcode_dict: Dict[int, Optional[Barcode]] = {}

    if barcode_reading:
      resp_list = resp.split("bb/")[-1].split("/")  # remove header

      if len(resp_list) != no_container_per_carrier:
        raise ValueError(
          f"Number of barcodes read ({len(resp_list)}) does not match "
          f"expected number ({no_container_per_carrier})"
        )
      for i in range(0, no_container_per_carrier):
        if resp_list[i] == "00":
          barcode_dict[i] = None
        else:
          barcode_dict[i] = Barcode(
            data=resp_list[i], symbology=barcode_symbology, position_on_resource="right"
          )

    return barcode_dict

  # -- barcode commands ------------------------------------------------------

  async def set_1d_barcode_type(
    self,
    barcode_symbology: Optional[Barcode1DSymbology],
  ) -> None:
    """Set 1D barcode type for autoload barcode reading (C0:CB)."""

    if barcode_symbology is None:
      barcode_symbology = self._default_1d_symbology

    if barcode_symbology is None:
      raise RuntimeError("barcode_symbology is None after fallback to default")

    await self.driver.send_command(
      module="C0",
      command="CB",
      bt=self.barcode_1d_symbology_dict[barcode_symbology],
    )

    self._default_1d_symbology = barcode_symbology

  async def load_carrier_from_tray_and_scan_carrier_barcode(
    self,
    carrier_end_rail: int,
    carrier_barcode_reading: bool = True,
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    barcode_position: float = 4.3,  # mm
    barcode_reading_window_width: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/sec
  ) -> Optional[Barcode]:
    """Load carrier from loading tray and optionally scan 1D carrier barcode (C0:CI).

    Args:
      carrier_end_rail: End rail position of the carrier.
    """

    if barcode_symbology is None:
      barcode_symbology = self._default_1d_symbology

    if barcode_symbology is None:
      raise RuntimeError("barcode_symbology is None after fallback to default")

    carrier_end_rail_str = str(carrier_end_rail).zfill(2)

    if not (1 <= int(carrier_end_rail_str) <= 54):
      raise ValueError(f"carrier_end_rail must be between 1 and 54, got {carrier_end_rail}")
    if not (0 <= barcode_position <= 470):
      raise ValueError(f"barcode_position must be between 0 and 470, got {barcode_position}")
    if not (0.1 <= barcode_reading_window_width <= 99.9):
      raise ValueError(
        f"barcode_reading_window_width must be between 0.1 and 99.9, "
        f"got {barcode_reading_window_width}"
      )
    if not (1.5 <= reading_speed <= 160.0):
      raise ValueError(f"reading_speed must be between 1.5 and 160.0, got {reading_speed}")

    try:
      resp = await self.driver.send_command(
        module="C0",
        command="CI",
        cp=carrier_end_rail_str,
        bi=f"{round(barcode_position * 10):04}",
        bw=f"{round(barcode_reading_window_width * 10):03}",
        co="0960",  # Distance between containers (pattern) [0.1 mm]
        cv=f"{round(reading_speed * 10):04}",
      )
    except Exception as e:
      if carrier_barcode_reading:
        await self.move_to_safe_z_position()
        raise RuntimeError(
          f"Failed to load carrier at rail {carrier_end_rail} and scan barcode: {e}"
        )
      else:
        pass

    if not carrier_barcode_reading:
      return None

    barcode_str = resp.split("bb/")[-1]

    return Barcode(data=barcode_str, symbology=barcode_symbology, position_on_resource="right")

  # -- high-level load / unload ----------------------------------------------

  async def load_carrier(
    self,
    carrier_end_rail: int,
    carrier_barcode_reading: bool = True,
    barcode_reading: bool = False,
    barcode_reading_direction: Literal["horizontal", "vertical"] = "horizontal",
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    no_container_per_carrier: int = 5,
    reading_position_of_first_barcode: float = 63.0,  # mm
    distance_between_containers: float = 96.0,  # mm
    width_of_reading_window: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/secs
    park_autoload_after: bool = True,
  ) -> dict:
    """Use autoload to load carrier.

    Args:
      carrier_end_rail: End rail position of the carrier (1-54).
      carrier_barcode_reading: Whether to read the carrier barcode. Default True.
      barcode_reading: Whether to read container barcodes. Default False.
      barcode_reading_direction: Either "vertical" or "horizontal", default "horizontal".
      barcode_symbology: Barcode symbology. Default "Code 128 (Subset B and C)".
      no_container_per_carrier: Number of containers per carrier. Default 5.
      park_autoload_after: Whether to park autoload after loading. Default True.
    """

    if barcode_symbology is None:
      barcode_symbology = self._default_1d_symbology

    if not (1 <= carrier_end_rail <= 54):
      raise ValueError("carrier loading rail must be between 1 and 54")

    # Determine presence of carrier at defined position
    presence_check = await self.request_presence_of_single_carrier_on_loading_tray(carrier_end_rail)

    if presence_check != 1:
      raise ValueError(
        f"""No carrier found at position {carrier_end_rail},
                       have you placed the carrier onto the correct autoload tray position?"""
      )

    # Scan carrier barcode
    carrier_barcode = await self.load_carrier_from_tray_and_scan_carrier_barcode(
      carrier_end_rail, carrier_barcode_reading=carrier_barcode_reading
    )

    # Load carrier
    if barcode_reading:
      await self.set_1d_barcode_type(barcode_symbology=barcode_symbology)
      self._default_1d_symbology = barcode_symbology

      resp = await self.load_carrier_from_belt(
        barcode_reading=barcode_reading,
        barcode_reading_direction=barcode_reading_direction,
        barcode_symbology=barcode_symbology,
        reading_position_of_first_barcode=reading_position_of_first_barcode,
        no_container_per_carrier=no_container_per_carrier,
        distance_between_containers=distance_between_containers,
        width_of_reading_window=width_of_reading_window,
        reading_speed=reading_speed,
        park_autoload_after=False,
      )
    else:
      resp = await self.load_carrier_from_belt(
        barcode_reading=False, park_autoload_after=False
      )

    if park_autoload_after:
      await self.park()

    output = {
      "carrier_barcode": carrier_barcode if carrier_barcode_reading else None,
      "container_barcodes": resp if barcode_reading else None,
    }

    return output

  async def unload_carrier(
    self,
    carrier_end_rail: int,
    park_autoload_after: bool = True,
  ):
    """Use autoload to unload carrier (C0:CR).

    Args:
      carrier_end_rail: End rail position of the carrier (1-54).
    """

    if not (1 <= carrier_end_rail <= 54):
      raise ValueError("carrier loading rail must be between 1 and 54")

    carrier_end_rail_str = str(carrier_end_rail).zfill(2)

    resp = await self.driver.send_command(
      module="C0",
      command="CR",
      cp=carrier_end_rail_str,
    )

    if park_autoload_after:
      await self.park()

    return resp

  # -- LED / monitoring ------------------------------------------------------

  async def set_loading_indicators(self, bit_pattern: List[bool], blink_pattern: List[bool]):
    """Set loading indicators (LEDs) (C0:CP).

    Args:
      bit_pattern: On if True, off otherwise. Length 54.
      blink_pattern: Blinking if True, steady otherwise. Length 54.
    """

    if len(bit_pattern) != 54:
      raise ValueError(f"bit_pattern must be length 54, got {len(bit_pattern)}")
    if len(blink_pattern) != 54:
      raise ValueError(f"blink_pattern must be length 54, got {len(blink_pattern)}")

    def pattern2hex(pattern: List[bool]) -> str:
      bit_string = "".join(["1" if x else "0" for x in pattern])
      return hex(int(bit_string, base=2))[2:].upper().zfill(14)

    bit_pattern_hex = pattern2hex(bit_pattern)
    blink_pattern_hex = pattern2hex(blink_pattern)

    return await self.driver.send_command(
      module="C0",
      command="CP",
      cl=bit_pattern_hex,
      cb=blink_pattern_hex,
    )

  async def set_carrier_monitoring(self, should_monitor: bool = False):
    """Set carrier monitoring (C0:CU).

    Args:
      should_monitor: whether carrier should be monitored.
    """

    return await self.driver.send_command(module="C0", command="CU", cu=should_monitor)

  async def verify_and_wait_for_carriers(
    self,
    carrier_rails: List[Tuple[int, int]],
    check_interval: float = 1.0,
  ):
    """Verify that carriers have been loaded at expected rail positions.

    Checks if carriers are physically present on the deck at the specified
    rail positions using the deck's presence sensors. If any carriers are missing, it will:
    1. Prompt the user to load the missing carriers
    2. Flash LEDs at the missing positions using set_loading_indicators
    3. Continue checking until all carriers are detected

    Args:
      carrier_rails: List of (start_rail, end_rail) tuples for expected carriers.
      check_interval: Interval in seconds between presence checks (default: 1.0)

    Raises:
      ValueError: If carrier_rails is empty.
    """

    if len(carrier_rails) == 0:
      raise ValueError("No carriers found on deck. Assign carriers to the deck.")

    # The presence detection reports the end rail position
    expected_end_rails = [end_rail for _, end_rail in carrier_rails]

    # Check initial presence
    detected_rails = set(await self.request_presence_of_carriers_on_deck())
    missing_end_rails = sorted(set(expected_end_rails) - detected_rails)

    if len(missing_end_rails) == 0:
      logger.info(f"All carriers detected at end rail positions: {expected_end_rails}")
      await self.set_loading_indicators(
        bit_pattern=[False] * 54,
        blink_pattern=[False] * 54,
      )
      print(f"\n✓ All carriers successfully detected at end rail positions: {expected_end_rails}\n")
      return

    # Prompt user about missing carriers
    print(
      f"\n{'=' * 60}\n"
      f"CARRIER LOADING REQUIRED\n"
      f"{'=' * 60}\n"
      f"Expected carriers at end rail positions: {expected_end_rails}\n"
      f"Detected carriers at rail positions: {sorted(detected_rails)}\n"
      f"Missing carriers at end rail positions: {missing_end_rails}\n"
      f"{'=' * 60}\n"
      f"Please load the missing carriers. LEDs will flash at the carrier positions.\n"
      f"The system will automatically detect when all carriers are loaded.\n"
      f"{'=' * 60}\n"
    )

    # Flash LEDs until all carriers are detected
    while missing_end_rails:
      bit_pattern = [False] * 54
      blink_pattern = [False] * 54

      for missing_end_rail in missing_end_rails:
        for start_rail, end_rail in carrier_rails:
          if end_rail == missing_end_rail:
            for rail in range(start_rail, end_rail + 1):
              if 1 <= rail <= 54:
                indicator_index = rail - 1
                bit_pattern[indicator_index] = True
                blink_pattern[indicator_index] = True
            break

      await self.set_loading_indicators(bit_pattern[::-1], blink_pattern[::-1])

      await asyncio.sleep(check_interval)

      detected_rails = set(await self.request_presence_of_carriers_on_deck())
      missing_end_rails = sorted(set(expected_end_rails) - detected_rails)

    logger.info(f"All carriers successfully detected at end rail positions: {expected_end_rails}")
    await self.set_loading_indicators(
      bit_pattern=[False] * 54,
      blink_pattern=[False] * 54,
    )
    print("\n✓ All carriers successfully loaded and detected!\n")
