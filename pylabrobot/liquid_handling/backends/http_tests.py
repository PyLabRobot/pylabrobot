import unittest

import responses
from responses import matchers

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import HTTPBackend
from pylabrobot.resources.hamilton import STARLetDeck
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  HTF_L,
  Cos_96_EZWash,
  no_tip_tracking,
  no_volume_tracking
)

header_match = matchers.header_matcher({"User-Agent": "pylabrobot/0.1.0"})


class TestHTTPBackendCom(unittest.IsolatedAsyncioTestCase):
  """ Tests for setup and stop """
  def setUp(self) -> None:
    self.deck = STARLetDeck()
    self.backend = HTTPBackend("localhost", 8080, num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)

  @responses.activate
  async def test_setup_stop(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/setup",
      json={"status": "ok"},
      match=[header_match],
      status=200,
    )
    responses.add(
      responses.POST,
      "http://localhost:8080/events/stop",
      match=[header_match],
      json={"status": "ok"},
      status=200,
    )
    responses.add(
      responses.POST,
      "http://localhost:8080/events/resource-assigned",
      match=[header_match],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.setup()
    await self.lh.stop()


class TestHTTPBackendOps(unittest.IsolatedAsyncioTestCase):
  """ Tests for liquid handling ops. """

  @responses.activate
  async def asyncSetUp(self) -> None:
    responses.add(
      responses.POST,
      "http://localhost:8080/events/setup",
      json={"status": "ok"},
      status=200,
    )
    responses.add(
      responses.POST,
      "http://localhost:8080/events/resource-assigned",
      match=[header_match],
      json={"status": "ok"},
      status=200,
    )

    self.deck = STARLetDeck()
    self.tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
    self.tip_carrier[0] = self.tip_rack = HTF_L(name="tiprack")
    self.plate_carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.plate_carrier[0] = self.plate = Cos_96_EZWash(name="plate")
    self.deck.assign_child_resource(self.tip_carrier, rails=3)
    self.deck.assign_child_resource(self.plate_carrier, rails=15)

    self.backend = HTTPBackend("localhost", 8080, num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)

    await self.lh.setup()

  @responses.activate
  async def asyncTearDown(self) -> None:
    await super().asyncTearDown()
    responses.add(
      responses.POST,
      "http://localhost:8080/events/stop",
      json={"status": "ok"},
      status=200,
    )
    await self.lh.stop()

  @responses.activate
  async def test_tip_pickup(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "tiprack_tipspot_0_0",
            "tip": self.tip_rack.get_tip("A1").serialize()
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.pick_up_tips(self.tip_rack["A1"])

  @responses.activate
  async def test_tip_drop(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/drop-tips",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "tiprack_tipspot_0_0",
            "tip": self.tip_rack.get_tip("A1").serialize()
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )

    with no_tip_tracking():
      self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
      await self.lh.drop_tips(self.tip_rack["A1"])

  @responses.activate
  async def test_aspirate(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/aspirate",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "plate_well_0_0",
            "volume": 10,
            "flow_rate": "default",
            "liquid_height": "default",
            "blow_out_air_volume": 0,
            "tip": self.tip_rack.get_tip("A1").serialize(),
            "liquid_class": "WATER"
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    well = self.plate.get_item("A1")
    well.tracker.set_used_volume(10)
    await self.lh.aspirate([well], 10)

  @responses.activate
  async def test_dispense(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/dispense",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "plate_well_0_0",
            "volume": 10,
            "flow_rate": "default",
            "liquid_height": "default",
            "blow_out_air_volume": 0,
            "tip": self.tip_rack.get_tip("A1").serialize(),
            "liquid_class": "WATER"
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], 10)

  @responses.activate
  async def test_pick_up_tips96(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
          "offset": {"x": 0, "y": 0, "z": 0},
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.pick_up_tips96(self.tip_rack)

  @responses.activate
  async def test_drop_tips96(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/drop-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
          "offset": {"x": 0, "y": 0, "z": 0},
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.drop_tips96(self.tip_rack)

  @responses.activate
  async def test_aspirate96(self):
    # FIXME: pick up tips first, but make nicer.
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
          "offset": {"x": 0, "y": 0, "z": 0},
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.pick_up_tips96(self.tip_rack)

    responses.add(
      responses.POST,
      "http://localhost:8080/events/aspirate96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "aspiration": {
            "resource_name": "plate",
            "volume": 10,
            "flow_rate": "default",
            "offset": "default",
            "liquid_height": "default",
            "blow_out_air_volume": 0,
            "tips": [tip.serialize() for tip in self.tip_rack.get_all_tips()],
            "liquid_class": "WATER"
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )

    await self.lh.aspirate_plate(self.plate, 10)

  @responses.activate
  async def test_dispense96(self):
    # FIXME: pick up tips first, but make nicer.
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
          "offset": {"x": 0, "y": 0, "z": 0},
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    await self.lh.pick_up_tips96(self.tip_rack)

    responses.add(
      responses.POST,
      "http://localhost:8080/events/dispense96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "dispense": {
            "resource_name": "plate",
            "volume": 10,
            "flow_rate": "default",
            "offset": "default",
            "liquid_height": "default",
            "blow_out_air_volume": 0,
            "tips": [tip.serialize() for tip in self.tip_rack.get_all_tips()],
            "liquid_class": "WATER"
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )

    await self.lh.dispense_plate(self.plate, 10)
