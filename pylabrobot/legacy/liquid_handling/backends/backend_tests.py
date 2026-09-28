import inspect
import unittest
from typing import Type

import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.chatterbox import (
  LiquidHandlerChatterboxBackend,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton import HamiltonLiquidHandler
from pylabrobot.legacy.liquid_handling.backends.hamilton.nimbus_backend import NimbusBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import STAR, STARBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox import (
  STARChatterboxBackend,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp_backend import HamiltonTCPBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.vantage_backend import (
  Vantage,
  VantageBackend,
)
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.legacy.liquid_handling.backends.opentrons_chatterbox import (
  OpentronsOT2ChatterboxBackend,
)
from pylabrobot.legacy.liquid_handling.backends.opentrons_simulator import OpentronsOT2Simulator
from pylabrobot.legacy.liquid_handling.backends.serializing_backend import SerializingBackend
from pylabrobot.legacy.liquid_handling.backends.tecan.EVO_backend import (
  EVO,
  EVOBackend,
  TecanLiquidHandler,
)
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.temperature_controlling.opentrons_backend import (
  OpentronsTemperatureModuleBackend,
)
from pylabrobot.legacy.temperature_controlling.opentrons_backend_usb import (
  OpentronsTemperatureModuleUSBBackend,
)
from pylabrobot.legacy.thermocycling.opentrons_backend import OpentronsThermocyclerBackend
from pylabrobot.legacy.thermocycling.opentrons_backend_usb import OpentronsThermocyclerUSBBackend
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Deck


class _ValidationBackend(LiquidHandlerChatterboxBackend):
  pass


@pytest.mark.parametrize(
  "backend_type",
  [
    LiquidHandlerChatterboxBackend,
    HamiltonLiquidHandler,
    HamiltonTCPBackend,
    STARBackend,
    STAR,
    STARChatterboxBackend,
    NimbusBackend,
    VantageBackend,
    Vantage,
  ],
)
def test_native_liquid_handler_uses_managed_lifecycle_methods(
  backend_type: Type[MachineBackend],
) -> None:
  assert backend_type.setup is HasLifespan.setup
  assert backend_type.stop is HasLifespan.stop


def test_liquid_handler_backend_hierarchy_is_fully_native() -> None:
  assert inspect.isabstract(TecanLiquidHandler)
  assert TecanLiquidHandler.setup is HasLifespan.setup
  assert TecanLiquidHandler.stop is HasLifespan.stop

  assert inspect.isabstract(HamiltonLiquidHandler)
  assert HamiltonLiquidHandler.setup is HasLifespan.setup
  assert HamiltonLiquidHandler.stop is HasLifespan.stop
  assert inspect.isabstract(HamiltonTCPBackend)
  assert HamiltonTCPBackend.setup is HasLifespan.setup
  assert HamiltonTCPBackend.stop is HasLifespan.stop

  concrete_backend_types: list[Type[MachineBackend]] = [
    LiquidHandlerChatterboxBackend,
    STARBackend,
    STAR,
    STARChatterboxBackend,
    NimbusBackend,
    VantageBackend,
    Vantage,
    OpentronsOT2Backend,
    OpentronsOT2Simulator,
    OpentronsOT2ChatterboxBackend,
    OpentronsTemperatureModuleBackend,
    OpentronsTemperatureModuleUSBBackend,
    OpentronsThermocyclerBackend,
    OpentronsThermocyclerUSBBackend,
    EVOBackend,
    EVO,
  ]
  for backend_type in concrete_backend_types:
    assert not inspect.isabstract(backend_type)
    assert backend_type.setup is HasLifespan.setup
    assert backend_type.stop is HasLifespan.stop


class LiquidHandlerBackendTests(unittest.TestCase):
  def setUp(self) -> None:
    self.backend = _ValidationBackend(num_channels=4)

  def test_validation_requires_deck(self) -> None:
    with self.assertRaisesRegex(RuntimeError, "^Deck not set$"):
      self.backend._validate_lifespan_context()

  def test_validation_requires_head(self) -> None:
    self.backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))

    with self.assertRaisesRegex(RuntimeError, "^Head not set$"):
      self.backend._validate_lifespan_context()

  def test_validation_accepts_deck_and_head(self) -> None:
    self.backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
    self.backend.set_heads({0: TipTracker(thing="Channel 0")})

    self.backend._validate_lifespan_context()

  def test_chatterbox_serialization_round_trip(self) -> None:
    expected = {"type": "LiquidHandlerChatterboxBackend", "num_channels": 4}
    backend = LiquidHandlerChatterboxBackend(num_channels=4)

    self.assertEqual(backend.serialize(), expected)
    restored = LiquidHandlerBackend.deserialize(expected.copy())
    self.assertEqual(restored.serialize(), expected)

  def test_resource_free_backends_own_native_lifespans(self) -> None:
    self.assertFalse(inspect.isabstract(LiquidHandlerChatterboxBackend))
    self.assertIs(LiquidHandlerChatterboxBackend.setup, HasLifespan.setup)
    self.assertIs(LiquidHandlerChatterboxBackend.stop, HasLifespan.stop)
    self.assertTrue(inspect.isabstract(SerializingBackend))
    self.assertIs(SerializingBackend.setup, HasLifespan.setup)
    self.assertIs(SerializingBackend.stop, HasLifespan.stop)
