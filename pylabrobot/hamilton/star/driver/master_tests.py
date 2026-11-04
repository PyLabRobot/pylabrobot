import contextlib
import dataclasses
import json
import logging
import pathlib
import tempfile
import unittest
import unittest.mock
from typing import List, cast

import anyio
import pytest

import pylabrobot.hamilton.star.driver.master as master
import pylabrobot.hamilton.star.driver.simulator as simulator
from pylabrobot.concurrency import AsyncExitStackWithShielding, SiblingTaskErrors
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import RECORDING_STAR, RECORDING_STAR_HEAD384
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.io.io import IOBase
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck, STARLetDeck
from pylabrobot.serializer import serialize
from pylabrobot.testing.concurrency import AnyioTestBase

# The device this package ships a recording of, read through the one reader there is: tests need a
# device to start from, and this is the one they stand in for.
RECORDED_DEVICE = cast(DeviceConfiguration, read_configuration(RECORDING_STAR)["device"])


class TestSimulationLifespan(AnyioTestBase):
  """A simulated STAR owns its session without opening a physical link."""

  async def test_scoped_entry_and_reuse(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    for _ in range(2):
      async with driver:
        assert driver.connected
        assert driver.configuration is not None
        assert driver.x_arm is driver.left_x_arm
      assert not driver.connected

  async def test_failed_discovery_releases_session(self):
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    driver.simulated_configuration = dataclasses.replace(
      driver.simulated_configuration, autoload_installed=False
    )
    with pytest.raises(ValueError, match="autoload_installed"):
      async with driver:
        pass
    assert not driver.connected

  async def test_feature_initialization_reports_all_failures(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """One failed module does not cancel another, and setup reports both failures."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    arm_failure = ValueError("arm failed")
    autoload_failure = RuntimeError("autoload failed")

    async def fail_arm(*args: object) -> None:
      raise arm_failure

    async def fail_autoload(self: Autoload, park_after: bool = True) -> None:
      del self, park_after
      raise autoload_failure

    monkeypatch.setattr(driver, "_initialize_arm", fail_arm)
    monkeypatch.setattr(Autoload, "initialize", fail_autoload)

    with pytest.raises(SiblingTaskErrors) as raised:
      async with driver:
        pytest.fail("failed feature initialization reached the body")

    assert raised.value.exceptions == (arm_failure, autoload_failure)
    assert not driver.connected

  async def test_teardown_attempts_all_safe_z_moves_after_failures(
    self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
  ) -> None:
    """A failed safety move does not cancel an independent subsystem's move."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    attempted: List[str] = []

    async def fail_channels() -> None:
      attempted.append("channels")
      raise RuntimeError("channels stuck")

    async def fail_wheel() -> None:
      attempted.append("wheel")
      raise RuntimeError("wheel stuck")

    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    async with driver:
      assert driver.left_x_arm is not None and driver.left_x_arm.pipettes is not None
      assert driver.autoload is not None
      monkeypatch.setattr(driver.left_x_arm.pipettes, "move_to_safe_z", fail_channels)
      monkeypatch.setattr(driver.autoload, "wheel_move_to_safe_z", fail_wheel)

    assert set(attempted) == {"channels", "wheel"}
    assert (
      sum(record.message == "could not move a subsystem to Z safety" for record in caplog.records)
      == 2
    )
    assert not driver.connected


class _SoftwareLink(IOBase):
  """Transport recorder that can never contact a device."""

  def __init__(self, events: List[str]):
    super().__init__()
    self.events = events

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs):
    self.events.append("open")
    stack.callback(self.events.append, "close")

  async def read(self, *args, **kwargs) -> bytes:
    await anyio.sleep(0.01)
    raise TimeoutError

  async def write(self, data: bytes, *args, **kwargs) -> None:
    self.events.append("write")


class _SoftwareReplyReader:
  """Supply the test link's deliberately failing reply source without USB framing."""

  def __init__(self, io: _SoftwareLink) -> None:
    self.io = io

  async def read_reply(self) -> bytes:
    """Wait for the test link's read failure."""
    return await self.io.read(64)


class _SoftwareSTAR(master.STARDriver):
  """Drive the real owner entry sequence without firmware commands."""

  def __init__(self, events: List[str]):
    io = _SoftwareLink(events)
    super().__init__(io=io, reply_reader=_SoftwareReplyReader(io))
    self.events = events

  async def discover(self) -> None:
    self.events.append("discover")

  async def request_initialization_status(self, module: str = "C0") -> bool:
    return True

  async def initialize(self, read_timeout: int = 300) -> bool:
    del read_timeout
    return True

  async def _make_safe(self) -> None:
    self.events.append("safe")

  def format_setup_summary(self) -> str:
    return "software"


class TestRealOwnerLifespan(AnyioTestBase):
  """The real owner joins its reader and makes the machine safe before link closure."""

  async def test_close_after_safety(self):
    events: List[str] = []
    driver = _SoftwareSTAR(events)
    async with driver:
      assert driver.connected
      assert driver.io.is_active
    assert events == ["open", "discover", "safe", "close"]
    assert not driver.connected

  async def test_failed_entry_still_runs_safety_before_close(self):
    events: List[str] = []
    driver = _SoftwareSTAR(events)

    async def fail_discovery() -> None:
      events.append("discover")
      raise ValueError("discovery failed")

    driver.discover = fail_discovery  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="discovery failed"):
      async with driver:
        pass
    assert events == ["open", "discover", "safe", "close"]
    assert not driver.connected


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
  """Each tip model gets one entry in the device's tip type table, never the grip tool's."""

  def setUp(self):
    self.driver = master.STARDriver.__new__(master.STARDriver)
    self.driver._tip_type_indices = {}
    self.define = unittest.mock.AsyncMock()
    self.driver.define_tip_needle = self.define  # type: ignore[method-assign]

  async def test_tips_of_one_kind_share_one_index(self):
    from pylabrobot.resources.hamilton import hamilton_tip_10uL, hamilton_tip_300uL

    assign = self.driver.get_or_assign_tip_type_index
    a = await assign(hamilton_tip_300uL(name="rack_A1#0"))
    b = await assign(hamilton_tip_300uL(name="rack_B1#0"))
    c = await assign(hamilton_tip_10uL(name="rack_C1#0"))
    self.assertEqual((a, b, c), (1, 1, 2))
    self.assertEqual(self.define.await_count, 2)

  async def test_the_grip_tool_s_entry_is_skipped(self):
    from pylabrobot.resources.hamilton import hamilton_tip_300uL

    self.driver._tip_type_indices = {f"model_{i}": i for i in range(1, 14)}
    index = await self.driver.get_or_assign_tip_type_index(hamilton_tip_300uL(name="tip"))
    self.assertEqual(index, 15)
    self.assertEqual(self.define.await_args_list[0].kwargs["tip_type_table_index"], 15)

  async def test_a_full_table_is_refused_before_anything_is_written(self):
    from pylabrobot.resources.hamilton import hamilton_tip_300uL

    free = [i for i in range(1, 100) if i != master.CORE_GRIPPER_TIP_TYPE_INDEX]
    self.driver._tip_type_indices = {f"model_{i}": i for i in free}
    with self.assertRaisesRegex(ValueError, "the tip type table is full: 98 tip types"):
      await self.driver.get_or_assign_tip_type_index(hamilton_tip_300uL(name="tip"))
    self.define.assert_not_awaited()


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


class TestDeckSize(unittest.IsolatedAsyncioTestCase):
  """Discovery refuses a deck built for another frame before anything moves."""

  async def test_a_starlet_deck_on_a_star_is_refused_before_initialization(self):
    star = STARSimulationDriver(deck=STARLetDeck(), declared_configuration_json=RECORDING_STAR)
    sent: List[str] = []
    sent_before_stop: List[List[str]] = []
    send, finish = star.send_command, star._finish_session

    async def sending(module: str, command: str, *args, **kwargs):
      sent.append(module + command)
      return await send(module, command, *args, **kwargs)

    # The simulator builds its features up front, so the failure path's teardown has them to raise.
    async def finishing():
      sent_before_stop.append(list(sent))
      await finish()

    with unittest.mock.patch.object(star, "send_command", sending):
      with unittest.mock.patch.object(star, "_finish_session", finishing):
        with self.assertRaisesRegex(ValueError, "54 slots, the deck is built for 30"):
          await star.setup()
    self.assertEqual(sent_before_stop, [["C0RM", "C0QM", "C0RU", "C0UA"]])


class TestRepeatedSetup(unittest.IsolatedAsyncioTestCase):
  """Discovery can re-read a device within one open session."""

  async def test_a_second_discovery_does_not_open_the_link_again(self):
    star = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    with unittest.mock.patch.object(
      star.io, "_enter_lifespan", wraps=star.io._enter_lifespan
    ) as opened:
      async with star:
        await star.discover()
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
    # The autoload is its own unit and comes up alongside the arm's features, so where its two
    # steps fall among theirs is the event loop's business; that it homes after the device is not.
    autoload = [move for move in moves if "autoload" in move]
    self.assertEqual(autoload, ["II autoload", "autoload park"])
    if "VI device" in moves:
      self.assertGreater(moves.index("II autoload"), moves.index("VI device"))
    return [move for move in moves if "autoload" not in move]

  async def test_everything_already_up(self):
    self.assertEqual(
      await self.run_setup(device_up=True, head_up=True, eject_position=True),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "ZA channels to safe Z",
        "iSWAP park",
        "EV 96-head probe and retract",
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

  async def test_channels_left_as_they_are_still_have_their_pistons_read(self):
    """A device already up with nothing mounted skips the channels' initialization."""
    star = simulator.STARSimulationDriver(
      deck=STARDeck(), initialized=True, declared_configuration_json=RECORDING_STAR
    )
    with recorded_moves() as moves:
      await star.setup()
    self.assertNotIn("DI channels", moves)
    pipettes = star.pipettes
    assert pipettes is not None
    self.assertEqual(pipettes.piston_positions, [0.0] * pipettes.num_channels)

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
      head._position_centred_in(star.deck.get_resource("trash_core96")),
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
      ],
    )


class TestChannelResources(unittest.IsolatedAsyncioTestCase):
  """A channel is modelled by a resource of its own, and the list of them runs channel by channel."""

  async def test_a_channel_without_a_width_is_refused_rather_than_skipped(self):
    """Skipping one would put every later channel's resource one out of step with its channel."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    await driver.setup()
    pipettes = driver.pipettes
    assert pipettes is not None
    channels = [resource.name for resource in pipettes.resources]
    self.assertEqual(
      channels,
      [driver._component_name(f"pipette_channel_{channel}") for channel in range(len(channels))],
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
