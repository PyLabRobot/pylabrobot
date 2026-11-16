"""Mantis device tests: the parts tree and lifespan."""

import os

import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.formulatrix.mantis import Mantis, MantisDevice
from pylabrobot.formulatrix.mantis.driver.master_tests import FakeFTDI
from pylabrobot.testing.concurrency import AnyioTestBase

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class TestMantisDevice(AnyioTestBase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=FakeFTDI())

  def test_inherits_managed_lifespan_methods(self) -> None:
    device = self.make_device()
    assert isinstance(device, HasLifespan)
    assert MantisDevice.setup is HasLifespan.setup
    assert MantisDevice.stop is HasLifespan.stop
    assert MantisDevice.__aenter__ is HasLifespan.__aenter__
    assert MantisDevice.__aexit__ is HasLifespan.__aexit__

  def test_every_part_has_its_model_file(self) -> None:
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      assert os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb"))

  def test_simulation_refuses_a_link(self) -> None:
    with pytest.raises(ValueError):
      Mantis(simulation=True, io=FakeFTDI())
