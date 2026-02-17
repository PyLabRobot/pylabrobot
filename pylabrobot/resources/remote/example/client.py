"""Connect to a remote deck server and run a liquid handling workflow."""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import STARBackend
from pylabrobot.resources.remote.client import RemoteDeck


async def main():
    deck = RemoteDeck.connect("http://localhost:8080")
    lh = LiquidHandler(backend=STARBackend(), deck=deck)
    await lh.setup()

    tips = deck.get_resource("tips")
    plate = deck.get_resource("plate")

    await lh.pick_up_tips(tips["A1:H1"])
    await lh.aspirate(plate["A1:H1"], vols=[100.0] * 8)
    await lh.dispense(plate["A2:H2"], vols=[100.0] * 8)
    await lh.drop_tips(tips["A1:H1"])

    await lh.stop()


asyncio.run(main())
