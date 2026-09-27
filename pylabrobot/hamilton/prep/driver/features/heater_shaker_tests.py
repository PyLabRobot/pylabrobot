"""Tests for the Prep heater shaker: when it is built, what it checks, and what it sends."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, List

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.heater_shaker import PrepHamiltonHeaterShaker
from pylabrobot.hamilton.prep.driver.simulator import (
  FIRMWARE_TREE_V1_2_2,
  FIRMWARE_TREE_V3_0_20,
  FIRMWARE_TREE_V3_0_20_HEATER_SHAKER,
  RECORDING_PREP,
  RECORDING_PREP_HEATER_SHAKER,
)
from pylabrobot.resources.hamilton import PrepDeck


def _record_send(prep: PrepDriver) -> List[Any]:
  captured: List[Any] = []
  orig_send = prep.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.send_command = recording  # type: ignore[method-assign, assignment]
  return captured


def _declaration_with_heater_shaker() -> str:
  """The V1.2.2 declaration, with a heater shaker declared on a tree that has none."""
  with open(RECORDING_PREP, encoding="utf-8") as f:
    saved = json.load(f)
  saved["device"]["heater_shaker_installed"] = True
  path = os.path.join(tempfile.mkdtemp(), "prep_heater_shaker.json")
  with open(path, "w", encoding="utf-8") as f:
    json.dump(saved, f)
  return path


async def _prep(heater_shaker: bool = True) -> PrepSimulationDriver:
  """The recorded device with a heater shaker, or the V3.0.20 one without."""
  if heater_shaker:
    p = PrepSimulationDriver(
      deck=PrepDeck(),
      declared_configuration_json=RECORDING_PREP_HEATER_SHAKER,
      firmware_tree_json=FIRMWARE_TREE_V3_0_20_HEATER_SHAKER,
    )
  else:
    p = PrepSimulationDriver(
      deck=PrepDeck(),
      declared_configuration_json=RECORDING_PREP,
      firmware_tree_json=FIRMWARE_TREE_V3_0_20,
    )
  await p.setup()
  return p


def _hs(p: PrepDriver) -> PrepHamiltonHeaterShaker:
  assert p.hs is not None
  return p.hs


def test_no_heater_shaker_is_built_on_a_device_without_one():
  """The feature is None, and a heater shaker command is refused before it is sent."""

  async def run():
    p = await _prep(heater_shaker=False)
    assert p.hs is None
    assert await p.request_heater_shaker_installed() is False
    with pytest.raises(RuntimeError, match="did not resolve"):
      await p.send_command(PrepCmd.PrepHHSStopHeating())
    await p.stop()

  asyncio.run(run())


def test_setup_builds_it_reads_its_ranges_and_initializes_it():
  """Setup discovers the firmware and ranges, and initializes an uninitialized shaker."""

  async def run():
    p = PrepSimulationDriver(
      deck=PrepDeck(),
      declared_configuration_json=RECORDING_PREP_HEATER_SHAKER,
      firmware_tree_json=FIRMWARE_TREE_V3_0_20_HEATER_SHAKER,
    )
    captured = _record_send(p)
    await p.setup()
    c = _hs(p).configuration
    assert c.firmware_version == "S.1.00.01.0101 2018-05-02 (XRP Heater Shaker)"
    assert c.temperature_range == (0.0, 115.0)
    assert c.speed_range == (25.0, 2500.0)
    assert c.acceleration_range == (625.0, 12500.0)
    initializes = [x for x in captured if isinstance(x, PrepCmd.PrepHHSInitialize)]
    assert [x.smart for x in initializes] == [True]
    await p.stop()

  asyncio.run(run())


def test_a_heater_shaker_declared_on_a_tree_without_one_is_added():
  """The simulator adds its objects, so a declaration alone brings one up."""

  async def run():
    p = PrepSimulationDriver(
      deck=PrepDeck(),
      declared_configuration_json=_declaration_with_heater_shaker(),
      firmware_tree_json=FIRMWARE_TREE_V1_2_2,
    )
    await p.setup()
    await _hs(p).start_shaking(800)
    assert await _hs(p).request_is_shaking() is True
    await p.stop()

  asyncio.run(run())


def test_out_of_range_values_are_refused_before_anything_is_sent():
  """Speed, acceleration, temperature and supervision tolerance each have the device's range."""

  async def run():
    p = await _prep()
    hs = _hs(p)
    captured = _record_send(p)
    with pytest.raises(ValueError, match="speed"):
      await hs.start_shaking(3000)
    with pytest.raises(ValueError, match="acceleration"):
      await hs.start_shaking(800, acceleration=100)
    with pytest.raises(ValueError, match="temperature"):
      await hs.start_temperature_control(120)
    with pytest.raises(ValueError, match="supervision tolerance"):
      await hs.start_temperature_control(37, supervision_tolerance=200)
    assert captured == []
    await p.stop()

  asyncio.run(run())


def test_a_range_not_yet_read_is_refused():
  """Before `discover`, there is no range to check against."""
  hs = PrepHamiltonHeaterShaker(driver=None)  # type: ignore[arg-type]

  async def run():
    with pytest.raises(RuntimeError, match="call `discover` first"):
      await hs.start_shaking(800)

  asyncio.run(run())


def test_start_shaking_locks_first_and_sends_every_parameter():
  """The lock closes, then EnableShaking carries speed, time, lock, direction and acceleration."""

  async def run():
    p = await _prep()
    captured = _record_send(p)
    await _hs(p).start_shaking(800, direction="counter_clockwise", acceleration=2000)
    sent = [x for x in captured if not isinstance(x, PrepCmd.PrepHHSGetShakingStatus)]
    assert sent == [
      PrepCmd.PrepHHSSetPlateLockState(locked=True),
      PrepCmd.PrepHHSEnableShaking(
        shaking_speed=800,
        shaking_time=0,
        leave_locked=True,
        direction=PrepCmd.ShakeDirection.Reverse,
        acceleration=2000,
      ),
    ]
    await p.stop()

  asyncio.run(run())


def test_the_plate_is_not_unlocked_while_shaking():
  """Unlocking waits for `stop_shaking`."""

  async def run():
    p = await _prep()
    hs = _hs(p)
    await hs.start_shaking(800)
    with pytest.raises(RuntimeError, match="while shaking"):
      await hs.unlock_plate()
    await hs.stop_shaking()
    await hs.unlock_plate()
    assert await hs.request_plate_locked() is False
    await p.stop()

  asyncio.run(run())


def test_temperature_control_sends_the_target_and_its_supervision():
  """StartHeating carries the target, the wait, and the supervision timeout and tolerance."""

  async def run():
    p = await _prep()
    hs = _hs(p)
    captured = _record_send(p)
    await hs.start_temperature_control(37, supervision_timeout=600, supervision_tolerance=3)
    assert captured == [
      PrepCmd.PrepHHSStartHeating(
        target_temperature=37,
        wait_for_temperature_to_be_reached=False,
        supervision_timeout=600,
        supervision_tolerance=3,
      )
    ]
    assert await hs.wait_for_temperature() == pytest.approx(37.0)
    await p.stop()

  asyncio.run(run())


def test_waiting_for_a_temperature_never_set_is_refused():
  async def run():
    p = await _prep()
    with pytest.raises(RuntimeError, match="no target temperature"):
      await _hs(p).wait_for_temperature()
    await p.stop()

  asyncio.run(run())


def test_stop_stops_shaking_and_temperature_control():
  """The driver's stop leaves the heater shaker idle."""

  async def run():
    p = await _prep()
    hs = _hs(p)
    await hs.start_temperature_control(37)
    await hs.start_shaking(800)
    captured = _record_send(p)
    await p.stop()
    kinds = [type(x) for x in captured]
    assert PrepCmd.PrepHHSDisableShaking in kinds
    assert PrepCmd.PrepHHSStopHeating in kinds

  asyncio.run(run())


def test_a_device_with_a_heater_shaker_is_drawn_with_its_spot_cut_out():
  """The device's model switches to the frame with spot_0_3 cut out, and that model ships."""
  from pylabrobot.hamilton.prep.device import PREP_HEATER_SHAKER_MODEL, Prep

  async def run():
    prep = Prep(
      simulation=True,
      declared_configuration_json=RECORDING_PREP_HEATER_SHAKER,
      firmware_tree_json=FIRMWARE_TREE_V3_0_20_HEATER_SHAKER,
    )
    assert prep.model == "Prep"
    await prep.setup()
    assert prep.model == PREP_HEATER_SHAKER_MODEL
    await prep.stop()
    plain = Prep(
      simulation=True,
      declared_configuration_json=RECORDING_PREP,
      firmware_tree_json=FIRMWARE_TREE_V1_2_2,
    )
    await plain.setup()
    assert plain.model == "Prep"
    await plain.stop()

  asyncio.run(run())
  model_dir = os.path.join(os.path.dirname(__file__), "..", "..", "resource_model")
  assert os.path.isfile(os.path.join(model_dir, PREP_HEATER_SHAKER_MODEL + ".glb"))


def test_spot_0_3_becomes_the_heater_shakers_holder():
  """Same name; the heater shaker's footprint and place; what it held moves onto it."""
  from pylabrobot.hamilton.prep.driver.features.heater_shaker import (
    HEATER_SHAKER_FROM_SPOT,
    HEATER_SHAKER_PLATE_XY,
    HEATER_SHAKER_PLATE_Z,
    HEATER_SHAKER_SIZE,
  )
  from pylabrobot.resources import PlateHolder, Resource

  async def run():
    deck = PrepDeck()
    before = deck[3]
    assert before.name == "spot_0_3"
    plate = Resource(name="plate", size_x=127.76, size_y=85.48, size_z=14.0)
    before.assign_child_resource(plate)
    assert before.location is not None
    where = before.location
    p = PrepSimulationDriver(
      deck=deck,
      declared_configuration_json=RECORDING_PREP_HEATER_SHAKER,
      firmware_tree_json=FIRMWARE_TREE_V3_0_20_HEATER_SHAKER,
    )
    await p.setup()
    spot = deck.get_resource("spot_0_3")
    assert isinstance(spot, PlateHolder)
    assert spot is _hs(p).resource and spot is not before
    assert spot.location == where + HEATER_SHAKER_FROM_SPOT
    assert (spot.get_size_x(), spot.get_size_y()) == HEATER_SHAKER_SIZE
    assert spot.get_size_z() == HEATER_SHAKER_PLATE_Z
    assert spot.model is None
    assert plate.parent is spot
    placed = plate.get_location_wrt(deck)
    assert (placed.x, placed.y, placed.z) == pytest.approx(
      (
        where.x + HEATER_SHAKER_FROM_SPOT.x + HEATER_SHAKER_PLATE_XY[0],
        where.y + HEATER_SHAKER_FROM_SPOT.y + HEATER_SHAKER_PLATE_XY[1],
        HEATER_SHAKER_PLATE_Z,
      )
    )
    await p.stop()
    await p.setup()
    assert deck.get_resource("spot_0_3") is spot
    await p.stop()

  asyncio.run(run())


def test_spot_0_3_stays_a_spot_without_a_heater_shaker():
  async def run():
    deck = PrepDeck()
    before = deck.get_resource("spot_0_3")
    p = PrepSimulationDriver(
      deck=deck,
      declared_configuration_json=RECORDING_PREP,
      firmware_tree_json=FIRMWARE_TREE_V1_2_2,
    )
    await p.setup()
    assert deck.get_resource("spot_0_3") is before
    assert before.model == "hamilton_prep_resourceholder"
    await p.stop()

  asyncio.run(run())
