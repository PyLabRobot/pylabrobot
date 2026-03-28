"""Emergency tip removal — drops any mounted tips at the tip rack position.

Deck layout must match test_air_evo_pipette.py:
  Rail 16: MP_3Pos carrier, Position 3: tip rack

Usage:
  python keyser-testing/tips_off.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air
from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

logging.basicConfig(level=logging.WARNING)


async def main():
  print("Tips Off — dropping any mounted tips at tip rack position")

  backend = AirEVOBackend(diti_count=8)
  deck = EVO150Deck()

  # Same deck layout as test script
  carrier = MP_3Pos("carrier")
  deck.assign_child_resource(carrier, rails=16)
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[2] = tip_rack

  saved_prt = backend.io.packet_read_timeout
  backend.io.packet_read_timeout = 1
  await backend.io.setup()
  backend.io.packet_read_timeout = saved_prt

  from pylabrobot.liquid_handling.backends.tecan.EVO_backend import LiHa

  backend.liha = LiHa(backend, "C5")
  backend._num_channels = await backend.liha.report_number_tips()
  backend._x_range = await backend.liha.report_x_param(5)
  backend._y_range = (await backend.liha.report_y_param(5))[0]
  backend._z_range = (await backend.liha.report_z_param(5))[0]
  backend.set_deck(deck)

  # Check tip status
  resp = await backend.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"  Tip status (RTS): {tip_status} (0=no tips, 255=all tips)")

  if tip_status == 0:
    print("  No tips mounted.")
  else:
    print("  Tips detected, moving to tip rack and dropping...")

    # Move to tip rack position (same X calc as pick_up_tips with offset)
    spot = tip_rack.get_item("A1")
    loc = spot.get_location_wrt(deck) + spot.center()
    x = int((loc.x - 100) * 10) + 60  # same X offset as pick_up_tips
    ys = int(spot.get_absolute_size_y() * 10)
    y = int((346.5 - loc.y) * 10)

    await backend.liha.set_z_travel_height([backend._z_range] * backend._num_channels)
    await backend.liha.position_absolute_all_axis(
      x, y, ys, [backend._z_range] * backend._num_channels
    )

    # Drop tips
    try:
      await backend.send_command("C5", command="SDT1,1000,200")
      await backend.liha._drop_disposable_tip(255, discard_height=1)
      print("  Tips dropped!")
    except TecanError as e:
      print(f"  Drop failed: {e}")
      print("  Trying ADT (simple discard)...")
      try:
        await backend.send_command("C5", command="ADT255")
        print("  Tips dropped with ADT!")
      except TecanError as e2:
        print(f"  ADT also failed: {e2}")

  # Verify
  resp = await backend.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"  Final tip status: {tip_status}")

  await backend.io.stop()
  print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
