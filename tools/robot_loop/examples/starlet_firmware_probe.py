"""Minimal STARlet probe for the local robot loop runner.

This is intended as a safe first hardware script: setup, capture firmware context, and stop.
"""

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.resources import STARLetDeck


async def run(context):
  backend = STARBackend()
  context.register_backend(backend, label="starlet")

  lh = LiquidHandler(backend=backend, deck=STARLetDeck())
  try:
    await lh.setup()
    context.add_note("setup completed")
  finally:
    await lh.stop()
