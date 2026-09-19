import contextlib
import dataclasses
import json
import pathlib
import tempfile
import unittest
import unittest.mock
from typing import List, cast

import pylabrobot.hamilton.star.driver.master as master
import pylabrobot.hamilton.star.driver.simulator as simulator
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import RECORDING_STAR, RECORDING_STAR_HEAD384
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.serializer import serialize

# The device this package ships a recording of, read through the one reader there is: tests need a
# device to start from, and this is the one they stand in for.
RECORDED_DEVICE = cast(DeviceConfiguration, read_configuration(RECORDING_STAR)["device"])


def declaring(**parts: object) -> str:
  """The shipped recording with parts swapped out, written where it can be read back.

  A declaration is read from a file and nothing else, so a test that needs a device no recording
  describes writes one. Everything not named here stays as the recorded STAR has it.

  Args:
    parts: `device` for the device itself, or a feature name for something the left arm carries.

  Returns:
    The path it was written to.
  """
  tree = json.loads(pathlib.Path(RECORDING_STAR).read_text())
  for name, part in parts.items():
    assert dataclasses.is_dataclass(part) and not isinstance(part, type)
    if name == "device":
      tree["device"] = serialize(dataclasses.asdict(part))
    else:
      tree["arms"]["left"][name] = serialize(dataclasses.asdict(part))
  written = pathlib.Path(tempfile.mkdtemp()) / "declared.json"
  written.write_text(json.dumps(tree))
  return str(written)


def declaring_a_device(
  channels: int, head96: bool, head384: bool, iswap: bool, autoload: bool
) -> str:
  """A declaration for a STAR fitted with exactly these, written where it can be read back.

  Built from the shipped recording so every value is one a device answered: only what is fitted
  changes. The bits and the features are set together, since the bits are what the driver builds
  from and the features are what it builds.

  Args:
    channels: how many pipetting channels, 0 for none.
    head96: whether a 96-head is fitted.
    head384: whether a 384-head is fitted.
    iswap: whether an iSWAP is fitted.
    autoload: whether an autoload is fitted.

  Returns:
    The path it was written to.
  """
  tree = json.loads(pathlib.Path(RECORDING_STAR).read_text())
  device, arm, carried = tree["device"], tree["device"]["left_arm"], tree["arms"]["left"]

  device["num_pip_channels"] = channels
  arm["pip_installed"] = channels > 0
  device["head96_installed"] = arm["head96_installed"] = head96
  device["head384_installed"] = arm["head384_installed"] = head384
  device["kb_iswap_installed"] = arm["iswap_installed"] = iswap
  device["autoload_installed"] = autoload

  if head384:
    # A 384-head is declared the way every other feature here is, out of the recording of a device
    # fitted with one. Nothing stands in for a feature a declaration claims but does not describe.
    carried["head384"] = json.loads(pathlib.Path(RECORDING_STAR_HEAD384).read_text())["arms"][
      "left"
    ]["head384"]
  for name, fitted in (("head96", head96), ("iswap", iswap), ("pipettes", channels > 0)):
    if not fitted:
      carried.pop(name, None)
  if not autoload:
    tree.pop("autoload", None)
  if channels > 0:
    # One recorded channel stands for all of them: they are the same part.
    recorded = carried["pipettes"]["channels"]
    carried["pipettes"]["channels"] = (recorded * channels)[:channels]

  written = pathlib.Path(tempfile.mkdtemp()) / "declared.json"
  written.write_text(json.dumps(tree))
  return str(written)


class TestXArm(unittest.IsolatedAsyncioTestCase):
  """`STARDriver.x_arm` is the single-arm shorthand, and its job is which error it raises."""

  async def test_before_setup(self):
    with self.assertRaises(RuntimeError):
      STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR).x_arm

  async def test_one_arm(self):
    star = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    await star.setup()
    self.assertIs(star.x_arm, star.left_x_arm)

  async def test_two_arms(self):
    both = dataclasses.replace(
      RECORDED_DEVICE,
      right_arm=BARE_X_ARM,
    )
    star = STARSimulationDriver(
      deck=STARDeck(),
      declared_configuration_json=declaring(device=both),
    )
    await star.setup()
    with self.assertRaises(ValueError):
      star.x_arm

  async def test_no_arms(self):
    neither = dataclasses.replace(RECORDED_DEVICE, left_arm=None, right_arm=None)
    star = STARSimulationDriver(
      deck=STARDeck(),
      declared_configuration_json=declaring(device=neither),
    )
    await star.setup()
    with self.assertRaises(ValueError):
      star.x_arm


class TestSimulation(unittest.IsolatedAsyncioTestCase):
  """A simulated device has no firmware to ask, so the resource model is all it can answer from."""

  async def test_simulation_needs_a_deck(self):
    with self.assertRaises(ValueError):
      STARSimulationDriver()


class TestTipTypeTable(unittest.IsolatedAsyncioTestCase):
  async def test_tips_of_one_kind_share_one_index(self):
    from unittest.mock import AsyncMock

    from pylabrobot.resources.hamilton import hamilton_tip_10uL, hamilton_tip_300uL

    driver = master.STARDriver.__new__(master.STARDriver)
    driver._tip_type_indices = {}
    driver.define_tip_needle = AsyncMock()  # type: ignore[method-assign]
    a = await driver.get_or_assign_tip_type_index(hamilton_tip_300uL(name="rack_A1#0"))
    b = await driver.get_or_assign_tip_type_index(hamilton_tip_300uL(name="rack_B1#0"))
    c = await driver.get_or_assign_tip_type_index(hamilton_tip_10uL(name="rack_C1#0"))
    self.assertEqual((a, b, c), (1, 1, 2))
    self.assertEqual(driver.define_tip_needle.await_count, 2)


def keys_no_field_reads(saved: dict, configuration: object) -> List[str]:
  """What a saved configuration holds that reading it back leaves out, nested ones included.

  Args:
    saved: the configuration as JSON holds it.
    configuration: what reading it back built.

  Returns:
    Every key with no field to read it into, as a dotted path from `saved`.
  """
  fields = {field.name: field for field in dataclasses.fields(configuration)}  # type: ignore[arg-type]
  dropped = [key for key in saved if key not in fields]
  for key, value in saved.items():
    nested = getattr(configuration, key, None)
    if key in fields and isinstance(value, dict) and dataclasses.is_dataclass(nested):
      dropped += [f"{key}.{inner}" for inner in keys_no_field_reads(value, nested)]
  return dropped


class TestRecordings(unittest.TestCase):
  """What ships under recordings/ is read back whole: no key a configuration no longer has, and no
  window left empty for a head to be built on."""

  def test_every_key_is_a_field(self):
    for path in sorted(pathlib.Path(RECORDING_STAR).parent.glob("*.json")):
      saved = json.loads(path.read_text(encoding="utf-8"))
      read = read_configuration(str(path))
      sections = [("device", saved["device"], read["device"])]
      for side, carried in saved.get("arms", {}).items():
        sections += [
          (f"arms.{side}.{name}", value, read["arms"][side][name])
          for name, value in carried.items()
        ]
      if "autoload" in saved:
        sections.append(("autoload", saved["autoload"], read["autoload"]))
      for where, value, configuration in sections:
        with self.subTest(recording=path.name, section=where):
          self.assertEqual(keys_no_field_reads(value, configuration), [])

  def test_every_head_has_a_z_range(self):
    for path in sorted(pathlib.Path(RECORDING_STAR).parent.glob("*.json")):
      for side, carried in read_configuration(str(path))["arms"].items():
        for name in ("head96", "head384"):
          if name in carried:
            with self.subTest(recording=path.name, head=name):
              self.assertIsNotNone(carried[name].z_range)


class TestDeclaredConfiguration(unittest.IsolatedAsyncioTestCase):
  """A simulated device answers what it was declared to be, and discovery checks what it answered
  against the declaration as it does on a physical device."""

  async def test_setup_checks_what_the_device_answered(self):
    star = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    check = master.STARDriver._check_declared_against
    with unittest.mock.patch.object(
      master.STARDriver, "_check_declared_against", autospec=True, side_effect=check
    ) as checked:
      await star.setup()
    checked.assert_called_once()
    self.assertIsNot(star.configuration, star.declared["device"])

  async def test_a_device_that_is_not_what_was_declared_is_refused(self):
    star = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    star.simulated_configuration = dataclasses.replace(
      star.simulated_configuration, autoload_installed=False
    )
    with self.assertRaisesRegex(ValueError, "autoload_installed"):
      await star.setup()


class TestRepeatedSetup(unittest.IsolatedAsyncioTestCase):
  """Setup is repeatable: a second one re-reads the device over the link the first one opened."""

  async def test_a_second_setup_does_not_open_the_link_again(self):
    star = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    with unittest.mock.patch.object(star, "_open", wraps=star._open) as opened:
      await star.setup()
      await star.setup()
    self.assertEqual(opened.call_count, 1)


# What each initialization step is called in the sequences below, and where it is defined. The simulated
# classes override some of them, so each is recorded where a simulated run would reach it.
MOVING_STEPS = [
  (simulator.STARSimulationDriver, "_pre_initialize", "VI device"),
  (simulator.Pipettes, "probe_z_max", "ZA channels to safe Z"),
  (simulator.SimulatedPipettes, "initialize", "DI channels"),
  (simulator.SimulatedISWAP, "initialize", "FI iSWAP"),
  (simulator.iSWAP, "park", "iSWAP park"),
  (simulator._SimulatedHead, "initialize", "EI 96-head"),
  (simulator._SimulatedHead, "probe_z_max", "EV 96-head probe and retract"),
  (simulator.SimulatedAutoload, "initialize", "II autoload"),
  (Autoload, "park", "autoload park"),
]


@contextlib.contextmanager
def recorded_moves():
  """Record every setup step that moves the device, in the order setup runs them."""
  moves: List[str] = []
  with contextlib.ExitStack() as stack:
    for owner, name, label in MOVING_STEPS:
      real = owner.__dict__[name]

      def wrap(real=real, label=label):
        async def recorded(self, *args, **kwargs):
          moves.append(label)
          return await real(self, *args, **kwargs)

        return recorded

      stack.enter_context(unittest.mock.patch.object(owner, name, wrap()))
    yield moves


class TestSetupSequence(unittest.IsolatedAsyncioTestCase):
  """Setup moves the device, and the order it moves it in is what keeps the arm's modules from
  driving into each other. It follows the legacy routine: the channels reach Z safety and the head
  retracts before the iSWAP moves on the shared left X-drive, and the head is only initialized once
  its own status has been asked. The 96-head retract runs on every setup, since that retract is
  what keeps it clear."""

  async def run_setup(
    self, device_up: bool, head_up: bool, eject_position: bool, trash96: bool = True
  ) -> List[str]:
    star = simulator.STARSimulationDriver(
      deck=STARDeck(with_trash96=trash96),
      initialized=device_up,
      declared_configuration_json=RECORDING_STAR,
    )
    star.initialized["H0"] = head_up
    cast(Head96, star.head96).configuration.tip_discard_location = (
      Coordinate(-263.8, 108.3, 200.0) if eject_position else None
    )
    with recorded_moves() as moves:
      await star.setup()
    return list(moves)

  async def test_everything_already_up(self):
    self.assertEqual(
      await self.run_setup(device_up=True, head_up=True, eject_position=True),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "ZA channels to safe Z",
        "iSWAP park",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def test_head_down_on_a_device_that_is_up(self):
    self.assertEqual(
      await self.run_setup(device_up=True, head_up=False, eject_position=True),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "ZA channels to safe Z",
        "iSWAP park",
        "EI 96-head",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def test_head_down_with_nowhere_to_eject(self):
    """It is still retracted, because that is what keeps it clear of the iSWAP; it is just not
    initialized, since initializing throws off whatever is mounted and there is nowhere to drop it:
    no location of its own, and a deck with no trash for it."""
    self.assertEqual(
      await self.run_setup(device_up=True, head_up=False, eject_position=False, trash96=False),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "ZA channels to safe Z",
        "iSWAP park",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def warnings_after_setup(
    self, head_up: bool, eject_position: bool, trash96: bool = True
  ) -> List[str]:
    with unittest.mock.patch.object(master.logger, "warning") as warning:
      await self.run_setup(
        device_up=True, head_up=head_up, eject_position=eject_position, trash96=trash96
      )
    return [call.args[0] % call.args[1:] for call in warning.call_args_list]

  async def test_a_head_told_nowhere_to_eject_ejects_at_the_decks_trash(self):
    """A STAR deck carries a trash for the 96-head, and a head with no eject location of its own is
    initialized over it, centred, as legacy does."""
    star = simulator.STARSimulationDriver(
      deck=STARDeck(), initialized=True, declared_configuration_json=RECORDING_STAR
    )
    star.initialized["H0"] = False
    head = cast(Head96, star.head96)
    assert star.deck is not None

    with recorded_moves() as moves:
      await star.setup()

    self.assertIn("EI 96-head", moves)
    self.assertEqual(
      head.configuration.tip_discard_location,
      head._position_centred_in(star.deck.get_trash_area96()),
    )

  async def test_a_feature_left_down_is_named_once_setup_has_run(self):
    warnings = await self.warnings_after_setup(head_up=False, eject_position=False, trash96=False)
    self.assertTrue(
      any("setup finished with these not initialized" in w and "Head96" in w for w in warnings),
      warnings,
    )

  async def test_nothing_is_named_when_everything_came_up(self):
    warnings = await self.warnings_after_setup(head_up=True, eject_position=True)
    self.assertFalse(
      any("setup finished with these not initialized" in w for w in warnings), warnings
    )

  async def test_device_not_up(self):
    """The device procedure homes every drive, so nothing is raised beforehand. It runs alone:
    the autoload is its own unit, but bringing it up alongside puts a C0 command and an I0 command
    in flight together, and the device has been seen answering one of them with the id of the
    other's predecessor. It comes up with the rest of the features afterwards, as it does in
    legacy."""
    self.assertEqual(
      await self.run_setup(device_up=False, head_up=False, eject_position=True),
      [
        "VI device",
        "DI channels",
        "ZA channels to safe Z",
        "FI iSWAP",
        "iSWAP park",
        "EI 96-head",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )


class TestChannelResources(unittest.IsolatedAsyncioTestCase):
  """A channel is modelled by a resource of its own, and the list of them runs channel by channel."""

  async def test_a_channel_without_a_width_is_refused_rather_than_skipped(self):
    """Skipping one would put every later channel's resource one out of step with its channel."""
    from pylabrobot.hamilton.star.device import RECORDING_STAR
    from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
    from pylabrobot.resources.hamilton import STARDeck

    deck = STARDeck()
    driver = STARSimulationDriver(deck=deck, declared_configuration_json=RECORDING_STAR)
    await driver.setup()
    pipettes = driver.pipettes
    assert pipettes is not None
    channels = [resource.name for resource in pipettes.resources]
    self.assertEqual(
      channels, [deck.prefixed(f"pipette_channel_{channel}") for channel in range(len(channels))]
    )

    pipettes.configuration.channels[2].width = None
    for resource in list(pipettes.resources):
      resource.parent.unassign_child_resource(resource)  # type: ignore[union-attr]
    with self.assertRaises(RuntimeError):
      await driver._create_pipette_resources()
    await driver.stop()


class TestEveryConfiguration(unittest.IsolatedAsyncioTestCase):
  """Every combination of what a STAR can be fitted with, and what each one builds.

  What the declaration says is fitted is what the driver has to end up carrying, and nothing else.
  A feature built from a bit that was not set, or missing where one was, is the failure this is
  looking for.
  """

  async def test_each_combination_builds_exactly_what_it_declares(self):
    for channels in (4, 8, 12, 16):
      for head96 in (False, True):
        for head384 in (False, True):
          for iswap in (False, True):
            for autoload in (False, True):
              with self.subTest(
                channels=channels,
                head96=head96,
                head384=head384,
                iswap=iswap,
                autoload=autoload,
              ):
                driver = STARSimulationDriver(
                  deck=STARDeck(),
                  declared_configuration_json=declaring_a_device(
                    channels=channels,
                    head96=head96,
                    head384=head384,
                    iswap=iswap,
                    autoload=autoload,
                  ),
                )
                await driver.setup()
                arm = driver.x_arm
                self.assertEqual(arm.pipettes is not None, channels > 0)
                if channels > 0:
                  self.assertEqual(driver.num_channels, channels)
                self.assertEqual(arm.head96 is not None, head96)
                self.assertEqual(arm.head384 is not None, head384)
                self.assertEqual(arm.iswap is not None, iswap)
                self.assertEqual(driver.autoload is not None, autoload)
