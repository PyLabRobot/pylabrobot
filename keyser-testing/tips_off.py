"""Emergency tip removal — drops any mounted tips at the tip rack position.

Deck layout must match test scripts:
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
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware import LiHa

logging.basicConfig(level=logging.WARNING)


async def main():
  print("Tips Off — dropping any mounted tips at tip rack position")

  driver = TecanEVODriver(packet_read_timeout=1, read_timeout=30, write_timeout=30)
  deck = EVO150Deck()

  carrier = MP_3Pos("carrier")
  deck.assign_child_resource(carrier, rails=16)
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[2] = tip_rack

  await driver.setup()
  liha = LiHa(driver, "C5")
  num_ch = await liha.report_number_tips()
  z_range = (await liha.report_z_param(5))[0]

  # Check tip status
  resp = await driver.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"  Tip status (RTS): {tip_status} (0=no tips, 255=all tips)")

  if tip_status == 0:
    print("  No tips mounted.")
  else:
    print("  Tips detected, moving to tip rack and dropping...")

    spot = tip_rack.get_item("A1")
    loc = spot.get_location_wrt(deck) + spot.center()
    x = int((loc.x - 100) * 10)
    y = int((346.5 - loc.y) * 10)

    await liha.set_z_travel_height([z_range] * num_ch)
    await liha.position_absolute_all_axis(x, y, 90, [z_range] * num_ch)

    # Drop tips
    try:
      await driver.send_command("C5", command="SDT1,1000,200")
      await liha.drop_disposable_tip(255, discard_height=1)
      print("  Tips dropped!")
    except TecanError as e:
      print(f"  Drop failed: {e}")
      print("  Trying ADT (simple discard)...")
      try:
        await driver.send_command("C5", command="ADT255")
        print("  Tips dropped with ADT!")
      except TecanError as e2:
        print(f"  ADT also failed: {e2}")

  resp = await driver.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"  Final tip status: {tip_status}")

  await driver.stop()
  print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
