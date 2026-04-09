"""Drop mounted tips back into the tip box at column 1.

Uses the full TecanEVO device so calibration offsets are applied.

Deck layout:
  Rail 16: MP_3Pos carrier, Position 3: tip rack

Usage:
  python keyser-testing/tips_off_tipbox.py [tip_type] [column]

  tip_type: 50, 200, or 1000 (default: 50)
  column:   1-12 (default: 1)
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import (
  DiTi_50ul_SBS_LiHa_Air,
  DiTi_50ul_SBS_LiHa_Air_tip,
  DiTi_200ul_SBS_LiHa_Air,
  DiTi_200ul_SBS_LiHa_Air_tip,
  DiTi_1000ul_SBS_LiHa_Air,
  DiTi_1000ul_SBS_LiHa_Air_tip,
  MP_3Pos_Corrected,
)
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"]

TIP_CONFIGS = {
  "50": ("DiTi 50uL", DiTi_50ul_SBS_LiHa_Air, DiTi_50ul_SBS_LiHa_Air_tip),
  "200": ("DiTi 200uL", DiTi_200ul_SBS_LiHa_Air, DiTi_200ul_SBS_LiHa_Air_tip),
  "1000": ("DiTi 1000uL", DiTi_1000ul_SBS_LiHa_Air, DiTi_1000ul_SBS_LiHa_Air_tip),
}


async def main():
  # Parse args
  args = sys.argv[1:]
  tip_type = "50"
  col = 1

  if args:
    if args[0] in TIP_CONFIGS:
      tip_type = args[0]
      args = args[1:]
    if args:
      col = int(args[0])

  if tip_type not in TIP_CONFIGS:
    print(f"Unknown tip type: {tip_type}. Choose from: {', '.join(TIP_CONFIGS.keys())}")
    return

  if not 1 <= col <= 12:
    print(f"Invalid column: {col}. Must be 1-12.")
    return

  label, rack_fn, tip_fn = TIP_CONFIGS[tip_type]
  wells = [f"{row}{col}" for row in ROWS]

  print("=" * 60)
  print(f"  Tips Off — drop {label} tips into column {col}")
  print("=" * 60)

  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=False,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  carrier = MP_3Pos_Corrected("carrier")
  deck.assign_child_resource(carrier, rails=16)

  tip_rack = rack_fn("tips")
  carrier[2] = tip_rack

  print("\nInitializing...")
  await evo.setup()
  print("Ready!")

  # Check tip status
  resp = await evo._driver.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"\nTip status (RTS): {tip_status} (0=no tips, 255=all tips)")

  if tip_status == 0:
    print("No tips mounted — nothing to drop.")
  else:
    print(f"Tips detected, syncing tip tracker with {label}...")
    for ch in range(8):
      if not evo.pip.head[ch].has_tip:
        evo.pip.head[ch].add_tip(tip_fn())

    print(f"Dropping into tip box column {col}...")
    await evo.pip.drop_tips(tip_rack.get_items(wells))
    print("Tips dropped!")

    resp = await evo._driver.send_command("C5", command="RTS")
    tip_status = resp["data"][0] if resp and resp.get("data") else 0
    print(f"Final tip status: {tip_status}")

  # Raise channels to Z max
  pip_be = evo.pip.backend
  z_range = pip_be._z_range
  num_ch = pip_be.num_channels
  print("Raising channels to Z max...")
  z_params = ",".join([str(z_range)] * num_ch)
  await evo._driver.send_command("C5", command=f"PAZ{z_params}")
  print("Channels raised.")

  await evo.stop()
  print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
