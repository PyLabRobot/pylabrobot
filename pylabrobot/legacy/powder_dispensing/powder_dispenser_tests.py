import inspect
from typing import Any, List

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.powder_dispensing.backend import (
  DispenseResults,
  PowderDispense,
  PowderDispenserBackend,
)
from pylabrobot.legacy.powder_dispensing.chatterbox import PowderDispenserChatterboxBackend
from pylabrobot.legacy.powder_dispensing.chemspeed.crystal_powderdose import CrystalPowderdose
from pylabrobot.legacy.powder_dispensing.powder_dispenser import PowderDispenser
from pylabrobot.resources import Powder, cor_96_wellplate_360uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingPowderBackend(PowderDispenserBackend):
  def __init__(self) -> None:
    super().__init__()
    self.calls: list[tuple[list[PowderDispense], dict[str, Any]]] = []

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    if backend_kwargs:
      raise TypeError("RecordingPowderBackend does not accept lifespan options")

  async def dispense(
    self, dispense_parameters: List[PowderDispense], **backend_kwargs: Any
  ) -> List[DispenseResults]:
    self.calls.append((dispense_parameters, backend_kwargs))
    return [DispenseResults(actual_amount=item.amount) for item in dispense_parameters]


class SerializableCrystal(CrystalPowderdose):
  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    if backend_kwargs:
      raise TypeError("SerializableCrystal does not accept lifespan options")

  async def dispense(
    self, dispense_parameters: List[PowderDispense], **backend_kwargs: Any
  ) -> List[DispenseResults]:
    return []


def test_powder_backend_requires_dispense() -> None:
  assert inspect.isabstract(PowderDispenserBackend)
  assert PowderDispenserBackend.__abstractmethods__ == {"dispense"}
  assert PowderDispenserBackend.setup is HasLifespan.setup
  assert PowderDispenserBackend.stop is HasLifespan.stop


def test_crystal_powderdose_requires_dispense() -> None:
  assert inspect.isabstract(CrystalPowderdose)
  assert CrystalPowderdose.__abstractmethods__ == {"dispense"}
  assert CrystalPowderdose.setup is HasLifespan.setup
  assert CrystalPowderdose.stop is HasLifespan.stop


def test_crystal_powderdose_preserves_serialization() -> None:
  backend = SerializableCrystal(arksuite_address="http://arksuite.invalid")
  assert backend.serialize() == {
    "type": "SerializableCrystal",
    "arksuite_address": "http://arksuite.invalid",
  }


class TestPowderDispenser(AnyioTestBase):
  async def test_dispense_single_resource_normalizes_and_returns_actual_amount(self) -> None:
    backend = RecordingPowderBackend()
    dispenser = PowderDispenser(backend=backend)
    plate = cor_96_wellplate_360uL_Fb(name="test_resource")
    powder = Powder("salt")

    async with dispenser:
      result = await dispenser.dispense(plate["A1"], powder, 0.005)

    assert result == [{"actual_amount": 0.005}]
    assert len(backend.calls) == 1
    parameters, options = backend.calls[0]
    assert options == {}
    assert len(parameters) == 1
    assert parameters[0].resource is plate["A1"][0]
    assert parameters[0].powder is powder
    assert parameters[0].amount == 0.005

  async def test_dispense_multiple_resources_normalizes_once(self) -> None:
    backend = RecordingPowderBackend()
    dispenser = PowderDispenser(backend=backend)
    plate = cor_96_wellplate_360uL_Fb(name="test_resource")
    resources = plate["A1"] + plate["A2"]
    powders = [Powder("salt"), Powder("sugar")]

    async with dispenser:
      result = await dispenser.dispense(resources, powders, [0.005, 0.010])

    assert result == [{"actual_amount": 0.005}, {"actual_amount": 0.010}]
    assert len(backend.calls) == 1
    parameters, options = backend.calls[0]
    assert options == {}
    assert [parameter.resource for parameter in parameters] == resources
    assert [parameter.powder for parameter in parameters] == powders
    assert [parameter.amount for parameter in parameters] == [0.005, 0.010]

  async def test_dispense_forwards_backend_options(self) -> None:
    backend = RecordingPowderBackend()
    dispenser = PowderDispenser(backend=backend)
    plate = cor_96_wellplate_360uL_Fb(name="test_resource")

    async with dispenser:
      await dispenser.dispense(
        plate["A1"],
        Powder("salt"),
        0.005,
        custom_option="value1",
      )

    assert len(backend.calls) == 1
    _, options = backend.calls[0]
    assert options == {"custom_option": "value1"}

  async def test_assertion_for_mismatched_lengths(self) -> None:
    backend = RecordingPowderBackend()
    dispenser = PowderDispenser(backend=backend)
    plate = cor_96_wellplate_360uL_Fb(name="test_resource")

    async with dispenser:
      with pytest.raises(AssertionError):
        await dispenser.dispense(plate["A1"], [Powder("salt"), Powder("salt")], [0.005])

      with pytest.raises(AssertionError):
        await dispenser.dispense(
          plate["A1"],
          Powder("salt"),
          [0.005, 0.010],
          dispense_parameters=[{"param": "value"}, {"param": "value"}],
        )

    assert backend.calls == []


class TestPowderChatterbox(AnyioTestBase):
  async def test_powder_chatterbox_native_lifecycle_and_dispense_output(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = PowderDispenserChatterboxBackend()
    assert type(backend).setup is HasLifespan.setup
    assert type(backend).stop is HasLifespan.stop
    plate = cor_96_wellplate_360uL_Fb(name="test_resource")
    parameter = PowderDispense(resource=plate["A1"][0], powder=Powder("salt"), amount=0.005)
    async with backend:
      assert await backend.dispense([parameter]) == [{"actual_amount": 0.005}]
    assert capsys.readouterr().out.splitlines() == [
      "Setting up the powder dispenser.",
      "Dispensing 1 powders.",
      "Stopping the powder dispenser.",
    ]

  async def test_powder_chatterbox_cancelled_body_still_stops(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    entered = anyio.Event()
    backend = PowderDispenserChatterboxBackend()

    async def use_backend() -> None:
      async with backend:
        entered.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await entered.wait()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == (
      "Setting up the powder dispenser.\nStopping the powder dispenser.\n"
    )
    assert not backend.is_active

  async def test_powder_chatterbox_sequential_reuse(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = PowderDispenserChatterboxBackend()
    async with backend:
      pass
    async with backend:
      pass
    assert capsys.readouterr().out == (
      "Setting up the powder dispenser.\n"
      "Stopping the powder dispenser.\n"
      "Setting up the powder dispenser.\n"
      "Stopping the powder dispenser.\n"
    )

  async def test_powder_chatterbox_rejects_options_without_output(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = PowderDispenserChatterboxBackend()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")
    assert capsys.readouterr().out == ""
    assert not backend.is_active
