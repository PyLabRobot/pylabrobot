import unittest

import responses
from responses import matchers

from pylabrobot.liquid_handling import LiquidHandler, no_tip_tracking
from pylabrobot.liquid_handling.backends import HTTPBackend
from pylabrobot.liquid_handling.resources.hamilton import STARLetDeck
from pylabrobot.liquid_handling.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  HTF_L,
  Cos_96_EZWash
)

header_match = matchers.header_matcher({"User-Agent": "pylabrobot/0.1.0"})


class TestHTTPBackendCom(unittest.TestCase):
  """ Tests for setup and stop """
  def setUp(self) -> None:
    self.deck = STARLetDeck()
    self.backend = HTTPBackend("localhost", 8080, num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)

  @responses.activate
  def test_setup_stop(self):
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
    self.lh.setup()
    self.lh.stop()


class TestHTTPBackendOps(unittest.TestCase):
  """ Tests for liquid handling ops. """

  @responses.activate
  def setUp(self) -> None:
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

    self.lh.setup()

  @responses.activate
  def tearDown(self) -> None:
    responses.add(
      responses.POST,
      "http://localhost:8080/events/stop",
      json={"status": "ok"},
      status=200,
    )
    self.lh.stop()

  @responses.activate
  def test_tip_pickup(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "tiprack_tipspot_0_0",
            "tip": {
              "type": "HamiltonTip",
              "has_filter": True,
              "total_tip_length": 95.1,
              "maximal_volume": 1065,
              "fitting_depth": 8,
              "pickup_method": "OUT_OF_RACK",
              "tip_size": "HIGH_VOLUME"
            }
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.pick_up_tips(self.tip_rack["A1"])

  @responses.activate
  def test_tip_drop(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/drop-tips",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": "default",
            "resource_name": "tiprack_tipspot_0_0",
            "tip": {
              "type": "HamiltonTip",
              "has_filter": True,
              "total_tip_length": 95.1,
              "maximal_volume": 1065,
              "fitting_depth": 8,
              "pickup_method": "OUT_OF_RACK",
              "tip_size": "HIGH_VOLUME"
            }
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )

    with no_tip_tracking():
      self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
      self.lh.drop_tips(self.tip_rack["A1"])

  @responses.activate
  def test_aspirate(self):
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
            "liquid_height": 0,
            "blow_out_air_volume": 0
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.aspirate(self.plate["A1"], 10)

  @responses.activate
  def test_dispense(self):
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
            "liquid_height": 0,
            "blow_out_air_volume": 0
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.dispense(self.plate["A1"], 10)

  @responses.activate
  def test_pick_up_tips96(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/pick-up-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.pick_up_tips96(self.tip_rack)

  @responses.activate
  def test_drop_tips96(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/drop-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.drop_tips96(self.tip_rack)

  @responses.activate
  def test_aspirate96(self):
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
            "liquid_height": 0,
            "blow_out_air_volume": 0
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.aspirate_plate(self.plate, 10)

  @responses.activate
  def test_dispense96(self):
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
            "liquid_height": 0,
            "blow_out_air_volume": 0
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.dispense_plate(self.plate, 10)
