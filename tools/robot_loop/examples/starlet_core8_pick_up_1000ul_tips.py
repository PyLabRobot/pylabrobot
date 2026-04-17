"""Notebook-matched STARlet CoRe 8 tip pickup example for the robot loop runner.

This mirrors the setup in
/home/harleyk/Documents/GrindBio_Doris_Scripts/head_96w_pick_up_tips.ipynb
for a single hardware action:

- initialize the STARlet backend
- place the same carriers and labware on the deck
- pick up 1000 uL filtered tips on CoRe 8 channels 0-7 from A1:H1

Run with:

python -m tools.robot_loop.runner \
  --script tools/robot_loop/examples/starlet_core8_pick_up_1000ul_tips.py \
  --artifact-dir /tmp/starlet-runner-artifacts \
  --operation tips \
  --run-id core8_pickup_1000ul_a1_h1
"""

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import STARBackend
from pylabrobot.resources.agenbio import AGenBio_1_troughplate_190000uL_Fl
from pylabrobot.resources.bioer import BioER_96_wellplate_Vb_2200uL
from pylabrobot.resources.hamilton import (
  MFX_CAR_L5_base,
  STARLetDeck,
  TIP_CAR_480_A00,
  hamilton_96_tiprack_1000uL_filter,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_50uL_filter,
)
from pylabrobot.resources.hamilton.mfx_modules import Hamilton_MFX_plateholder_DWP_metal_tapped


async def run(context):
  backend = STARBackend()
  context.register_backend(backend, label="starlet")

  lh = LiquidHandler(backend=backend, deck=STARLetDeck())

  plateholder_0 = Hamilton_MFX_plateholder_DWP_metal_tapped(name="plateholder_0")
  plateholder_1 = Hamilton_MFX_plateholder_DWP_metal_tapped(name="plateholder_1")
  plateholder_2 = Hamilton_MFX_plateholder_DWP_metal_tapped(name="plateholder_2")
  plate_carrier = MFX_CAR_L5_base(
    name="plate_modules",
    modules={
      0: plateholder_0,
      1: plateholder_1,
      2: plateholder_2,
    },
  )

  tip_carrier = TIP_CAR_480_A00(name="tip_carrier_480")

  water_plate = AGenBio_1_troughplate_190000uL_Fl(name="water_plate")
  dw_plate = BioER_96_wellplate_Vb_2200uL(name="dw_plate")
  hi_orange = AGenBio_1_troughplate_190000uL_Fl(name="hiOrange")

  tip_rack_1000 = hamilton_96_tiprack_1000uL_filter(name="tiprack_1000ul_filter")
  tip_rack_50 = hamilton_96_tiprack_50uL_filter(name="tiprack_50ul_filter")
  tip_rack_300 = hamilton_96_tiprack_300uL_filter(name="tiprack_300ul_filter")

  plate_carrier[0] = dw_plate
  plate_carrier[1] = water_plate
  plate_carrier[2] = hi_orange

  tip_carrier[2] = tip_rack_1000
  tip_carrier[1] = tip_rack_50
  tip_carrier[0] = tip_rack_300

  lh.deck.assign_child_resource(tip_carrier, rails=13)
  lh.deck.assign_child_resource(plate_carrier, rails=7)

  try:
    await lh.setup(skip_autoload=True)
    context.add_note("setup complete")
    context.add_note("attempting CoRe 8 pickup from 1000 uL rack slot 2 on TIP_CAR_480_A00")

    await lh.pick_up_tips(
      tip_rack_1000["A1:H1"],
      use_channels=list(range(8)),
    )

    context.add_note("pick_up_tips completed")
  except Exception:
    try:
      faulty = await backend.request_name_of_last_faulty_parameter()
      context.write_json_artifact("last_faulty_parameter.json", faulty)
    except Exception as inner_exc:
      context.add_note(f"could not query last faulty parameter: {inner_exc}")
    raise
  finally:
    await lh.stop()
