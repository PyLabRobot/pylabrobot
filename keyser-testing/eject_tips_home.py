"""Eject tips at LiHa home position (in the air).

Connects directly to the driver, moves to home, and fires AST.

Usage:
  python keyser-testing/eject_tips_home.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.firmware import LiHa


async def main():
  driver = TecanEVODriver(packet_read_timeout=1, read_timeout=30, write_timeout=30)
  await driver.setup()

  liha = LiHa(driver, "C5")
  z_range = (await liha.report_z_param(5))[0]
  num_ch = await liha.report_number_tips()

  resp = await driver.send_command("C5", command="RTS")
  tip_status = resp["data"][0] if resp and resp.get("data") else 0
  print(f"Tip status: {tip_status} (0=none, 255=all)")

  if tip_status == 0:
    print("No tips mounted.")
  else:
    print("Moving to home position...")
    await liha.set_z_travel_height([z_range] * num_ch)
    await liha.position_absolute_all_axis(45, 1031, 90, [z_range] * num_ch)

    print("Ejecting tips...")
    await driver.send_command("C5", command="SDT0,50,200")
    await driver.send_command("C5", command="AST255,0")

    resp = await driver.send_command("C5", command="RTS")
    tip_status = resp["data"][0] if resp and resp.get("data") else 0
    print(f"Tip status after eject: {tip_status}")

  await driver.stop()
  print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
