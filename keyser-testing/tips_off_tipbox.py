"""Drop mounted tips back into the tip box at column 1.

Uses the full TecanEVO device so calibration offsets are applied.

Deck layout must match test scripts:
  Rail 16: MP_3Pos carrier, Position 3: tip rack

Usage:
  python keyser-testing/tips_off_tipbox.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted, MP_3Pos_Corrected
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

COLUMN_1 = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]


async def main():
  print("=" * 60)
  print("  Tips Off — drop tips back into tip box column 1")
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

  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("dest")
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
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
    print("Tips detected, syncing tip tracker...")
    # Tell PIP tracker that tips are mounted (it doesn't know from a prior session)
    from labware_library import DiTi_50ul_SBS_LiHa_Air_tip
    for ch in range(8):
      if not evo.pip.head[ch].has_tip:
        evo.pip.head[ch].add_tip(DiTi_50ul_SBS_LiHa_Air_tip())

    print("Dropping into tip box column 1...")
    await evo.pip.drop_tips(tip_rack.get_items(COLUMN_1))
    print("Tips dropped!")

    resp = await evo._driver.send_command("C5", command="RTS")
    tip_status = resp["data"][0] if resp and resp.get("data") else 0
    print(f"Final tip status: {tip_status}")

  # Raise channels to Z max (out of the way)
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
