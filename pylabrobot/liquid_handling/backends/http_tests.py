import unittest

import responses
from responses import matchers

from pylabrobot.liquid_handling import LiquidHandler
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
    self.backend = HTTPBackend("localhost", 8080)
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

    self.deck = STARLetDeck()
    self.tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
    self.tip_carrier[0] = self.tip_rack = HTF_L(name="tiprack")
    self.plate_carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
    self.plate_carrier[0] = self.plate = Cos_96_EZWash(name="plate")
    self.deck.assign_child_resource(self.tip_carrier, rails=3)
    self.deck.assign_child_resource(self.plate_carrier, rails=15)

    self.backend = HTTPBackend("localhost", 8080)
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
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            },
            "resource_name": "tiprack_tip_0_0",
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.pick_up_tips(self.tip_rack["A1"])

  @responses.activate
  def test_tip_discard(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/discard-tips",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            },
            "resource_name": "tiprack_tip_0_0",
          }],
          "use_channels": [0]
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.discard_tips(self.tip_rack["A1"])

  @responses.activate
  def test_aspirate(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/aspirate",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            },
            "resource_name": "plate_well_0_0",
            "volume": 10,
            "flow_rate": None
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.aspirate(self.plate["A1"], 10, liquid_classes=None)

  @responses.activate
  def test_dispense(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/dispense",
      match=[
        header_match,
        matchers.json_params_matcher({
          "channels": [{
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            },
            "resource_name": "plate_well_0_0",
            "volume": 10,
            "flow_rate": None
          }],
          "use_channels": [0],
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.dispense(self.plate["A1"], 10, liquid_classes=None)

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
  def test_discard_tips96(self):
    responses.add(
      responses.POST,
      "http://localhost:8080/events/discard-tips96",
      match=[
        header_match,
        matchers.json_params_matcher({
          "resource_name": "tiprack",
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.discard_tips96(self.tip_rack)

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
            "flow_rate": None,
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            }
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.aspirate_plate(self.plate, 10, liquid_class=None)

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
            "flow_rate": None,
            "offset": {
              "x": 0,
              "y": 0,
              "z": 0
            }
          }
        })
      ],
      json={"status": "ok"},
      status=200,
    )
    self.lh.dispense_plate(self.plate, 10, liquid_class=None)
