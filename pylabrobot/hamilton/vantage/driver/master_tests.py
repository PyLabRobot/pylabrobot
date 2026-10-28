import asyncio
import queue
import re
import unittest
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

from pylabrobot.events import EventBus, PLREvent
from pylabrobot.events.bus import use_event_bus
from pylabrobot.hamilton.transport.text_tcp import HamiltonTextTCP
from pylabrobot.hamilton.vantage.driver.configuration import DeviceConfiguration
from pylabrobot.hamilton.vantage.driver.errors import VantageFirmwareError
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.hamilton.vantage.driver.simulator import NODES_WITH_IPG, NODES_WITHOUT_IPG
from pylabrobot.io.io import IOBase
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipPickupMethod, TipSize

SPONTANEOUS = 'A1AM__id0sm"Spontan Message"'
ROUTER_LOGGER = "pylabrobot.hamilton.protocol.text.router"

T = TypeVar("T")


def echo(command: str, parameters: str = "", error: int = 0) -> str:
  """The reply a machine gives to a command: the same module, command and id, but with the id
  written without padding."""
  found = re.match(r"(\w{6})id(\d+)", command)
  assert found is not None, command
  return f"{found.group(1)}id{int(found.group(2))}er{error}{parameters}"


class ScriptedIO(IOBase):
  """A link that answers what the test scripted, and records what was written to it."""

  def __init__(self, answers: Optional[Callable[[str], Optional[List[str]]]] = None):
    self.answers = answers or (lambda command: None)
    self.replies: "queue.Queue[bytes]" = queue.Queue()
    self.written: List[str] = []
    self.write_timeouts: List[Optional[float]] = []
    self.setups = 0
    self.stops = 0

  async def setup(self):
    self.setups += 1

  async def stop(self):
    self.stops += 1

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    command = data.decode()
    self.written.append(command)
    self.write_timeouts.append(timeout)
    for reply in self.answers(command) or []:
      self.replies.put(reply.encode())

  async def read(self, timeout: Optional[float] = None) -> bytes:
    try:
      return self.replies.get(timeout=0.01)
    except queue.Empty:
      raise TimeoutError from None

  def say(self, line: str) -> None:
    """Make the machine send a line."""
    self.replies.put(line.encode())


def arm_status(initialized: bool) -> Callable[[str], Optional[List[str]]]:
  """A machine whose arm reports `initialized`, and that accepts a pre-initialization."""

  def answers(command: str) -> Optional[List[str]]:
    if command.startswith("A1AMQW"):
      return [echo(command, f"qw{int(initialized)}")]
    if command.startswith("A1AMMI"):
      return [echo(command)]
    return None

  return answers


def machine(
  arm_initialized: bool = True,
  channels_initialized: bool = True,
  tips: Optional[List[bool]] = None,
  num_channels: int = 8,
  arm_type: Optional[int] = None,
  node_names: Optional[str] = None,
  channel_spacing: Optional[Tuple[int, int]] = (45, 45),
  ipg: bool = False,
  ipg_initialized: bool = True,
  ipg_parked: bool = True,
  ipg_plate: bool = False,
) -> Callable[[str], Optional[List[str]]]:
  """A machine with `num_channels` channels, of which `tips` carry a tip, that answers what setup
  sends.

  It has an IPG if `ipg` is set, which then is as `ipg_initialized`, `ipg_parked` and `ipg_plate`
  say. Otherwise it is an arm type without one (13). `arm_type` and `node_names` override what the
  arm says of itself. `channel_spacing` is the back and front spacing the arm reports for the
  channels, in 0.1 mm; None makes it refuse the question.

  Like the machine, it refuses to initialize or park the IPG, or to initialize the channels, until
  the arm has been initialized: it answers error 16.
  """
  carrying = tips if tips is not None else [False] * num_channels
  kind = arm_type if arm_type is not None else (6 if ipg else 13)
  nodes = node_names if node_names is not None else (NODES_WITH_IPG if ipg else NODES_WITHOUT_IPG)
  arm_up = [arm_initialized]

  def answers(command: str) -> Optional[List[str]]:
    module_and_command = command[:6]
    if module_and_command == "A1AMQW":
      return [echo(command, f"qw{int(arm_up[0])}")]
    if module_and_command == "A1AMQU":
      return [echo(command, f"kt{kind}ka1300kd1300ke0kh0")]
    if module_and_command == "A1AMQC":
      if channel_spacing is None:
        return [echo(command, 'et"Command: Unknown command"', error=30)]
      return [echo(command, f"kf{channel_spacing[0]}kg{channel_spacing[1]}kx0")]
    if module_and_command == "A1AMQN":
      return [echo(command, f'xn"{nodes}"')]
    if module_and_command == "A1PMQW":
      return [echo(command, f"qw{int(channels_initialized)}")]
    if module_and_command == "A1PMQA":
      return [echo(command, "rt" + " ".join(str(int(tip)) for tip in carrying))]
    if module_and_command == "A1RMQW":
      return [echo(command, f"qw{int(ipg_initialized)}")]
    if module_and_command == "A1RMRG":
      return [echo(command, f"rg{int(ipg_parked)}")]
    if module_and_command == "A1RMQA":
      return [echo(command, f"rt{int(ipg_plate)}")]
    if module_and_command == "A1AMMI":
      arm_up[0] = True
      return [echo(command)]
    if module_and_command in ("A1AMTT", "A1PMTP", "A1PMTR"):
      return [echo(command)]
    if module_and_command in ("A1PMDI", "A1RMDI", "A1RMGP"):
      return [echo(command, error=0 if arm_up[0] else 16)]
    return None

  return answers


async def until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
  """Wait for a condition, failing the test when it does not come true."""
  waited = 0.0
  while not condition():
    if waited >= timeout:
      raise AssertionError("the condition did not come true")
    await asyncio.sleep(0.005)
    waited += 0.005


async def within(awaitable: Awaitable[T], seconds: float = 10.0) -> T:
  """Wait for something that would otherwise wait for ever, failing the test when it is late."""
  return await asyncio.wait_for(awaitable, seconds)


class TestConstruction(unittest.TestCase):
  """How the driver is told where the machine is."""

  def test_a_host_cannot_be_combined_with_a_usb_link_or_what_describes_one(self):
    for arguments in (
      {"io": ScriptedIO()},
      {"device_address": 4},
      {"serial_number": "0000"},
    ):
      with self.assertRaises(ValueError, msg=str(arguments)):
        VantageDriver(host="192.0.2.10", **arguments)  # type: ignore[arg-type]

  def test_without_io_or_host_the_link_is_usb_to_a_vantage(self):
    driver = VantageDriver(serial_number="0000")
    fields = driver.io.serialize()
    self.assertEqual((fields["id_vendor"], fields["id_product"]), (0x08AF, 0x8003))
    self.assertEqual(fields["serial_number"], "0000")
    self.assertEqual(fields["device_address"], None)

  def test_a_host_leaves_the_link_to_setup_which_resolves_it(self):
    driver = VantageDriver(host="192.0.2.10")
    with self.assertRaises(RuntimeError):
      driver.io  # noqa: B018
    self.assertIn("192.0.2.10:24652", driver._describe_link())

  def test_the_number_of_channels_is_not_known_before_discovery(self):
    driver = VantageDriver(ScriptedIO())
    with self.assertRaises(RuntimeError):
      driver.num_channels  # noqa: B018
    driver.configuration = DeviceConfiguration(num_channels=8)
    self.assertEqual(driver.num_channels, 8)


class TestSendCommand(unittest.IsolatedAsyncioTestCase):
  """Assembling a command, sending it, and reading what comes back."""

  async def asyncSetUp(self):
    self.io = ScriptedIO(arm_status(True))
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()

  async def asyncTearDown(self):
    self.driver._router.stop()

  async def test_a_command_is_written_with_a_padded_id_and_its_reply_is_parsed(self):
    reply = await self.driver.send_command("A1AM", "QW", fmt={"qw": "int"})
    self.assertEqual(self.io.written, ["A1AMQWid0001"])
    self.assertEqual(reply, {"qw": 1, "id": 1})

  async def test_without_a_format_the_raw_reply_is_returned(self):
    self.assertEqual(await self.driver.send_command("A1AM", "QW"), "A1AMQWid1er0qw1")

  async def test_parameters_are_written_after_the_id(self):
    await self.driver.send_command("A1AM", "QW", xp=7095, ab=True)
    self.assertEqual(self.io.written, ["A1AMQWid0001xp7095ab1"])

  async def test_a_reply_that_reports_an_error_raises(self):
    self.io.answers = lambda command: [echo(command, 'et"Command: Unknown command"', error=30)]
    with self.assertRaises(VantageFirmwareError) as caught:
      await self.driver.send_command("A1PM", "QA", fmt={"rt": "[int]"})
    self.assertEqual(caught.exception.errors, {"Pip": "Command: Unknown command"})

  async def test_a_list_is_ended_where_the_machine_has_fewer_channels_than_it_carries(self):
    self.driver.configuration = DeviceConfiguration(num_channels=8)
    await self.driver.send_command("A1AM", "QW", xp=[1, 2])
    await self.driver.send_command("A1AM", "QW", xp=[1] * 8)
    self.assertEqual(self.io.written, ["A1AMQWid0001xp1 2&", "A1AMQWid0002xp1 1 1 1 1 1 1 1"])

  async def test_a_list_for_selected_channels_is_filled_in_for_the_others_with_its_first_value(
    self,
  ):
    self.driver.configuration = DeviceConfiguration(num_channels=4)
    await self.driver.send_command("A1AM", "QW", tip_pattern=[True, False, True, False], xp=[5, 6])
    self.assertEqual(self.io.written, ["A1AMQWid0001xp5 5 6 5"])

  async def test_a_command_without_a_list_needs_no_channel_count(self):
    await self.driver.send_command("A1AM", "QW")
    self.assertEqual(self.io.written, ["A1AMQWid0001"])

  async def test_a_list_before_the_channels_are_counted_is_refused(self):
    with self.assertRaises(RuntimeError):
      await self.driver.send_command("A1AM", "QW", xp=[1, 2])
    self.assertEqual(self.io.written, [])

  async def test_every_command_leaves_through_the_one_place_a_simulator_answers_at(self):
    seen: List[Tuple[str, Optional[int], bool]] = []

    async def transmit(cmd, id_, write_timeout, read_timeout, wait=True):
      seen.append((cmd, id_, wait))
      return "A1AMQWid1er0qw1" if wait else None

    self.driver._transmit = transmit  # type: ignore[method-assign]
    await self.driver.send_command("A1AM", "QW")
    await self.driver.send_raw_command("A1AMQWid0042")
    await self.driver.send_raw_command("A1AMQW", wait=False)
    self.assertEqual(
      seen,
      [("A1AMQWid0001", 1, True), ("A1AMQWid0042", 42, True), ("A1AMQW", None, False)],
    )
    self.assertEqual(self.io.written, [])

  async def test_timeouts_are_passed_on(self):
    await self.driver.send_command("A1AM", "QW", write_timeout=9)
    self.assertEqual(self.io.write_timeouts, [9])

  async def test_a_reply_that_never_comes_times_out(self):
    self.io.answers = lambda command: None
    with self.assertRaises(TimeoutError):
      await self.driver.send_command("A1AM", "QW", read_timeout=1)

  async def test_a_raw_command_is_written_as_given_and_its_reply_returned(self):
    self.assertEqual(await self.driver.send_raw_command("A1AMQWid0042"), "A1AMQWid42er0qw1")
    self.assertEqual(self.io.written, ["A1AMQWid0042"])

  async def test_a_raw_command_can_be_sent_without_waiting(self):
    self.assertIsNone(await self.driver.send_raw_command("A1AMQWid0042", wait=False))
    self.assertEqual(self.io.written, ["A1AMQWid0042"])

  async def test_a_line_the_machine_sent_unasked_does_not_answer_the_command(self):
    self.io.answers = lambda command: [SPONTANEOUS, echo(command, "qw1")]
    with self.assertNoLogs(ROUTER_LOGGER, "WARNING"):
      reply = await self.driver.send_command("A1AM", "QW", fmt={"qw": "int"})
    self.assertEqual(reply, {"qw": 1, "id": 1})

  async def test_nothing_is_sent_into_a_closed_link(self):
    await self.driver.stop()
    with self.assertRaises(RuntimeError):
      await self.driver.send_command("A1AM", "QW")
    with self.assertRaises(RuntimeError):
      await self.driver.send_raw_command("A1AMQWid0001")
    self.assertEqual(self.io.written, [])


class TestEvents(unittest.IsolatedAsyncioTestCase):
  """Each command is announced when it starts, and when it ends."""

  async def asyncSetUp(self):
    self.io = ScriptedIO(arm_status(True))
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    self.events: List[PLREvent] = []
    self.bus = EventBus()
    self.bus.subscribe(self.events.append)

  async def asyncTearDown(self):
    self.driver._router.stop()

  async def test_a_command_that_is_answered_emits_started_and_completed(self):
    with use_event_bus(self.bus):
      await self.driver.send_command("A1AM", "QW")
    self.assertEqual(
      [event.name for event in self.events],
      ["firmware.command.started", "firmware.command.completed"],
    )
    started, completed = (event.data for event in self.events)
    self.assertEqual(started["raw_command"], "A1AMQWid0001")
    self.assertEqual(started["transport"], "hamilton_usb")
    self.assertEqual((started["module"], started["command"]), ("A1AM", "QW"))
    self.assertEqual(completed["response"], "A1AMQWid1er0qw1")

  async def test_a_command_that_fails_emits_started_and_failed(self):
    self.io.answers = lambda command: [echo(command, 'et"Broken"', error=5)]
    with use_event_bus(self.bus):
      with self.assertRaises(VantageFirmwareError):
        await self.driver.send_command("A1AM", "QW")
    self.assertEqual(
      [event.name for event in self.events],
      ["firmware.command.started", "firmware.command.failed"],
    )
    self.assertEqual(self.events[1].data["error_type"], "VantageFirmwareError")


class TestOneCommandPerModule(unittest.IsolatedAsyncioTestCase):
  """A module runs one command at a time; different modules, and queries, do not wait."""

  async def asyncSetUp(self):
    self.io = ScriptedIO()
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    self.tasks: List["asyncio.Task"] = []

  async def asyncTearDown(self):
    self.driver._router.stop()
    for task in self.tasks:
      task.cancel()
    await asyncio.gather(*self.tasks, return_exceptions=True)

  def send(self, module: str, command: str) -> "asyncio.Task":
    task = asyncio.ensure_future(self.driver.send_command(module, command))
    self.tasks.append(task)
    return task

  def written_commands(self) -> List[str]:
    return [command[:6] for command in self.io.written]

  async def test_a_second_command_to_a_module_waits_for_the_first_to_be_answered(self):
    first, second = self.send("A1PM", "DI"), self.send("A1PM", "DA")
    await until(lambda: len(self.io.written) == 1)
    await asyncio.sleep(0.05)
    self.assertEqual(self.written_commands(), ["A1PMDI"])

    self.io.say(echo(self.io.written[0]))
    await first
    await until(lambda: len(self.io.written) == 2)
    self.assertEqual(self.written_commands(), ["A1PMDI", "A1PMDA"])
    self.io.say(echo(self.io.written[1]))
    await second

  async def test_commands_to_different_modules_go_together(self):
    self.send("A1PM", "DI")
    self.send("A1AM", "MI")
    await until(lambda: len(self.io.written) == 2)
    self.assertEqual(sorted(self.written_commands()), ["A1AMMI", "A1PMDI"])

  async def test_a_query_does_not_wait_for_a_command_running_on_its_module(self):
    self.send("A1PM", "DI")
    await until(lambda: len(self.io.written) == 1)
    self.send("A1PM", "QW")
    self.send("A1PM", "RF")
    await until(lambda: len(self.io.written) == 3)
    self.assertEqual(self.written_commands(), ["A1PMDI", "A1PMQW", "A1PMRF"])

  async def test_a_command_that_fails_does_not_hold_its_module(self):
    first = self.send("A1PM", "DI")
    await until(lambda: len(self.io.written) == 1)
    self.io.say(echo(self.io.written[0], 'et"Broken"', error=5))
    with self.assertRaises(VantageFirmwareError):
      await first
    self.send("A1PM", "DA")
    await until(lambda: len(self.io.written) == 2)


class TestSetup(unittest.IsolatedAsyncioTestCase):
  """Connecting, finding out what the machine has, and bringing it up."""

  def driver_on(self, io: ScriptedIO) -> VantageDriver:
    return VantageDriver(io, read_timeout=5)

  def sent(self, io: ScriptedIO) -> List[str]:
    return [command[:6] for command in io.written]

  async def test_a_machine_that_is_up_is_read_and_left_alone(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertTrue(driver.connected)
      self.assertEqual(io.setups, 1)
      self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW"])
      self.assertEqual(driver.num_channels, 8)
    finally:
      await driver.stop()

  async def test_an_arm_that_reports_itself_down_is_pre_initialized_before_the_channels(self):
    io = ScriptedIO(machine(arm_initialized=False, channels_initialized=False))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(
        self.sent(io),
        ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1AMMI", "A1PMQW", "A1PMDI"],
      )
    finally:
      await driver.stop()

  async def test_channels_that_report_themselves_down_are_initialized(self):
    io = ScriptedIO(machine(channels_initialized=False))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(
        self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW", "A1PMDI"]
      )
    finally:
      await driver.stop()

  async def test_channels_that_are_up_but_carry_a_tip_are_initialized_to_discard_it(self):
    io = ScriptedIO(machine(tips=[False, True] + [False] * 6))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(
        self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW", "A1PMDI"]
      )
    finally:
      await driver.stop()

  async def test_the_tips_are_read_once_and_not_again_after_the_arm_initialized(self):
    io = ScriptedIO(machine(arm_initialized=False, tips=[True] * 8))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(self.sent(io).count("A1PMQA"), 1)
    finally:
      await driver.stop()

  async def test_the_number_of_channels_is_what_the_machine_reports(self):
    io = ScriptedIO(machine(num_channels=4))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(driver.num_channels, 4)
      self.assertEqual(
        driver.configuration,
        DeviceConfiguration(num_channels=4, arm_type=13, ipg_installed=False),
      )
    finally:
      await driver.stop()

  async def test_connect_opens_the_link_and_sends_nothing(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    try:
      await driver.connect()
      await driver.connect()
      self.assertTrue(driver.connected)
      self.assertEqual((io.setups, io.written), (1, []))
    finally:
      await driver.stop()

  async def test_a_connected_machine_can_be_read_without_being_brought_up(self):
    io = ScriptedIO(machine(arm_initialized=False, channels_initialized=False))
    driver = self.driver_on(io)
    try:
      await driver.connect()
      tips = await driver.discover()
      self.assertEqual(tips, [0] * 8)
      self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC"])
    finally:
      await driver.stop()

  async def test_setup_goes_on_over_a_link_that_connect_opened(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    try:
      await driver.connect()
      await driver.setup()
      self.assertEqual(io.setups, 1)
      self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW"])
    finally:
      await driver.stop()

  async def test_setup_is_repeatable_over_the_link_it_opened(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    try:
      await driver.setup()
      await driver.setup()
      self.assertEqual(io.setups, 1)
      self.assertEqual(
        self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW"] * 2
      )
    finally:
      await driver.stop()

  async def test_a_setup_that_fails_closes_the_link_and_raises_what_failed(self):
    io = ScriptedIO(lambda command: [echo(command, 'et"Broken"', error=5)])
    driver = self.driver_on(io)
    with self.assertRaises(RuntimeError):
      await driver.setup()
    self.assertFalse(driver.connected)
    self.assertEqual(io.stops, 1)

  async def test_a_failure_while_initializing_closes_the_link_too(self):
    base = machine(channels_initialized=False)

    def answers(command: str) -> Optional[List[str]]:
      if command.startswith("A1PMDI"):
        return [echo(command, 'et"Broken"', error=5)]
      return base(command)

    io = ScriptedIO(answers)
    driver = self.driver_on(io)
    with self.assertRaises(VantageFirmwareError):
      await driver.setup()
    self.assertFalse(driver.connected)
    self.assertEqual(io.stops, 1)

  async def test_channels_that_the_machine_does_not_know_are_refused_with_a_clear_message(self):
    def answers(command: str) -> Optional[List[str]]:
      if command.startswith("A1PM"):
        return [echo(command, 'et"Command: Unknown command"', error=30)]
      return machine()(command)

    io = ScriptedIO(answers)
    driver = self.driver_on(io)
    with self.assertRaises(RuntimeError) as caught:
      await driver.setup()
    self.assertIn("A1PM", str(caught.exception))
    self.assertIn("not supported", str(caught.exception))
    self.assertIsInstance(caught.exception.__cause__, VantageFirmwareError)
    self.assertEqual(self.sent(io), ["A1PMQA"])
    self.assertFalse(driver.connected)

  async def test_a_machine_that_reports_no_channel_is_refused(self):
    io = ScriptedIO(
      lambda command: [echo(command, "rt ")] if command.startswith("A1PMQA") else None
    )
    driver = self.driver_on(io)
    with self.assertRaises(RuntimeError) as caught:
      await driver.setup()
    self.assertIn("no pipetting channel", str(caught.exception))
    self.assertFalse(driver.connected)

  async def test_a_link_that_does_not_open_leaves_the_driver_disconnected(self):
    io = ScriptedIO()

    async def refuse():
      raise ConnectionError("no route")

    io.setup = refuse  # type: ignore[method-assign]
    driver = self.driver_on(io)
    with self.assertRaises(ConnectionError):
      await driver.setup()
    self.assertFalse(driver.connected)

  async def test_stop_closes_the_link_and_is_repeatable(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    await driver.setup()
    await driver.stop()
    await driver.stop()
    self.assertFalse(driver.connected)
    self.assertEqual(io.stops, 1)

  async def test_stop_without_setup_does_nothing(self):
    io = ScriptedIO()
    await self.driver_on(io).stop()
    self.assertEqual(io.stops, 0)

  async def test_initialize_needs_a_machine_that_was_discovered(self):
    driver = self.driver_on(ScriptedIO(machine()))
    with self.assertRaises(RuntimeError):
      await driver.initialize()

  async def test_the_arm_and_the_channels_are_given_time_to_initialize(self):
    io = ScriptedIO(machine(arm_initialized=False, channels_initialized=False))
    driver = self.driver_on(io)
    seen: Dict[str, Optional[int]] = {}
    send = driver._router.send

    async def spying(cmd, id_, write_timeout=None, read_timeout=None, wait=True):
      seen[cmd[:6]] = read_timeout
      return await send(cmd, id_, write_timeout, read_timeout, wait)

    driver._router.send = spying  # type: ignore[method-assign]
    try:
      await driver.setup()
      self.assertEqual(seen["A1AMMI"], 300)
      self.assertEqual(seen["A1PMDI"], 300)
      self.assertIsNone(seen["A1AMQW"])
    finally:
      await driver.stop()


class TestIpgDuringSetup(unittest.IsolatedAsyncioTestCase):
  """An IPG that is fitted is looked for before anything moves, and brought to a safe state
  before the arm and the channels move."""

  def driver_on(self, io: ScriptedIO) -> VantageDriver:
    return VantageDriver(io, read_timeout=5)

  def sent(self, io: ScriptedIO) -> List[str]:
    return [command[:6] for command in io.written]

  def rejecting(
    self, base: Callable[[str], Optional[List[str]]], *rejected: str
  ) -> Callable[[str], Optional[List[str]]]:
    """A machine that answers as `base`, except that it rejects the commands named."""

    def answers(command: str) -> Optional[List[str]]:
      if command[:6] in rejected:
        return [echo(command, 'et"Command: Unknown command"', error=30)]
      return base(command)

    return answers

  async def test_discover_finds_an_ipg_and_moves_nothing(self):
    io = ScriptedIO(machine(ipg=True, ipg_initialized=False, ipg_parked=False))
    driver = self.driver_on(io)
    try:
      await driver.connect()
      await driver.discover()
      self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC"])
      self.assertTrue(driver.configuration.ipg_installed)  # type: ignore[union-attr]
      self.assertEqual(driver.configuration.arm_type, 6)  # type: ignore[union-attr]
      self.assertIsNotNone(driver.ipg)
    finally:
      await driver.stop()

  async def test_a_machine_without_an_ipg_is_never_asked_about_one(self):
    io = ScriptedIO(machine())
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertFalse(driver.configuration.ipg_installed)  # type: ignore[union-attr]
      self.assertIsNone(driver.ipg)
      self.assertFalse([command for command in self.sent(io) if command.startswith("A1RM")])
    finally:
      await driver.stop()

  async def test_an_ipg_that_is_up_and_parked_is_read_and_left_alone(self):
    io = ScriptedIO(machine(ipg=True))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(
        self.sent(io),
        ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1RMQA", "A1AMQW", "A1RMQW", "A1RMRG", "A1PMQW"],
      )
    finally:
      await driver.stop()

  async def test_an_ipg_that_is_down_is_initialized_and_parked_after_the_arm_and_before_the_channels(
    self,
  ):
    io = ScriptedIO(
      machine(
        ipg=True,
        ipg_initialized=False,
        ipg_parked=False,
        arm_initialized=False,
        channels_initialized=False,
      )
    )
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertEqual(
        self.sent(io),
        [
          "A1PMQA",
          "A1AMQU",
          "A1AMQN",
          "A1AMQC",
          "A1RMQA",
          "A1AMQW",
          "A1AMMI",
          "A1RMQW",
          "A1RMDI",
          "A1RMRG",
          "A1RMGP",
          "A1PMQW",
          "A1PMDI",
        ],
      )
    finally:
      await driver.stop()

  async def test_an_ipg_that_is_up_but_not_parked_is_only_parked(self):
    io = ScriptedIO(machine(ipg=True, ipg_parked=False))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      sent = self.sent(io)
      self.assertIn("A1RMGP", sent)
      self.assertNotIn("A1RMDI", sent)
    finally:
      await driver.stop()

  async def test_an_ipg_that_is_down_but_parked_is_only_initialized(self):
    io = ScriptedIO(machine(ipg=True, ipg_initialized=False))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      sent = self.sent(io)
      self.assertIn("A1RMDI", sent)
      self.assertNotIn("A1RMGP", sent)
    finally:
      await driver.stop()

  async def test_an_ipg_that_holds_a_plate_stops_the_setup_before_anything_moves(self):
    io = ScriptedIO(
      machine(
        ipg=True,
        ipg_plate=True,
        ipg_initialized=False,
        ipg_parked=False,
        arm_initialized=False,
        channels_initialized=False,
      )
    )
    driver = self.driver_on(io)
    with self.assertRaises(RuntimeError) as caught:
      await driver.setup()
    self.assertIn("holds a plate", str(caught.exception))
    self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1RMQA"])
    self.assertFalse(driver.connected)

  async def test_the_ipg_is_given_time_to_initialize_and_park(self):
    io = ScriptedIO(machine(ipg=True, ipg_initialized=False, ipg_parked=False))
    driver = self.driver_on(io)
    seen: Dict[str, Optional[int]] = {}
    send = driver._router.send

    async def spying(cmd, id_, write_timeout=None, read_timeout=None, wait=True):
      seen[cmd[:6]] = read_timeout
      return await send(cmd, id_, write_timeout, read_timeout, wait)

    driver._router.send = spying  # type: ignore[method-assign]
    try:
      await driver.setup()
      self.assertEqual((seen["A1RMDI"], seen["A1RMGP"]), (300, 300))
      self.assertIsNone(seen["A1RMQW"])
    finally:
      await driver.stop()

  async def test_the_arm_type_alone_says_an_ipg_is_fitted(self):
    io = ScriptedIO(machine(arm_type=6, node_names=NODES_WITHOUT_IPG))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertIn("A1RMQW", self.sent(io))
    finally:
      await driver.stop()

  async def test_a_node_name_alone_says_an_ipg_is_fitted(self):
    io = ScriptedIO(machine(arm_type=13, node_names=NODES_WITH_IPG))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertIn("A1RMQW", self.sent(io))
    finally:
      await driver.stop()

  async def test_an_arm_type_that_has_none_and_nodes_that_have_none_say_there_is_none(self):
    io = ScriptedIO(machine(arm_type=9, node_names=NODES_WITHOUT_IPG))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      self.assertIsNone(driver.ipg)
    finally:
      await driver.stop()

  async def test_an_arm_that_does_not_report_its_type_is_judged_by_its_nodes(self):
    base = machine(ipg=True)
    io = ScriptedIO(self.rejecting(base, "A1AMQU"))
    driver = self.driver_on(io)
    try:
      with self.assertLogs("pylabrobot.hamilton.vantage.driver.master", "WARNING"):
        await driver.setup()
      self.assertIn("A1RMQW", self.sent(io))
      self.assertEqual(driver.configuration.arm_type, 0)  # type: ignore[union-attr]
    finally:
      await driver.stop()

  async def test_an_arm_that_does_not_report_its_nodes_is_judged_by_its_type(self):
    io = ScriptedIO(self.rejecting(machine(ipg=True), "A1AMQN"))
    driver = self.driver_on(io)
    try:
      with self.assertLogs("pylabrobot.hamilton.vantage.driver.master", "WARNING"):
        await driver.setup()
      self.assertIn("A1RMQW", self.sent(io))
    finally:
      await driver.stop()

  async def test_an_arm_that_answers_neither_question_is_refused_before_anything_moves(self):
    io = ScriptedIO(
      self.rejecting(
        machine(ipg=True, arm_initialized=False, channels_initialized=False),
        "A1AMQU",
        "A1AMQN",
      )
    )
    driver = self.driver_on(io)
    with self.assertLogs("pylabrobot.hamilton.vantage.driver.master", "WARNING"):
      with self.assertRaises(RuntimeError) as caught:
        await driver.setup()
    self.assertIn("internal plate gripper", str(caught.exception))
    self.assertEqual(self.sent(io), ["A1PMQA", "A1AMQU", "A1AMQN"])
    self.assertFalse(driver.connected)

  async def test_the_ipg_is_found_again_when_setup_is_repeated(self):
    io = ScriptedIO(machine(ipg=True))
    driver = self.driver_on(io)
    try:
      await driver.setup()
      first = driver.ipg
      await driver.setup()
      self.assertIs(driver.ipg, first)
      self.assertEqual(self.sent(io).count("A1RMQA"), 2)
    finally:
      await driver.stop()


class TestOverTCP(unittest.IsolatedAsyncioTestCase):
  """The same driver against a TCP server on this machine, lines ending in CRLF."""

  async def asyncSetUp(self):
    self.received: List[str] = []
    self.arm_initialized = True
    # The machine answers after this long, so a reader that can only take what is already
    # buffered would find nothing.
    self.answer_after = 0.05
    self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
    self.port = self.server.sockets[0].getsockname()[1]

  async def asyncTearDown(self):
    self.server.close()
    await self.server.wait_closed()

  async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
      # The machine speaks unasked, with no command waiting, and again before each answer.
      writer.write(SPONTANEOUS.encode() + b"\r\n")
      await writer.drain()
      while True:
        line = (await reader.readuntil(b"\r\n"))[:-2].decode()
        self.received.append(line)
        await asyncio.sleep(self.answer_after)
        writer.write(SPONTANEOUS.encode() + b"\r\n")
        if line.startswith("A1AMQW"):
          writer.write(echo(line, f"qw{int(self.arm_initialized)}").encode() + b"\r\n")
        elif line.startswith("A1AMQU"):
          writer.write(echo(line, "kt13ka497kd1257ke0kh106").encode() + b"\r\n")
        elif line.startswith("A1AMQN"):
          writer.write(echo(line, f'xn"{NODES_WITHOUT_IPG}"').encode() + b"\r\n")
        elif line.startswith("A1AMQC"):
          writer.write(echo(line, "kf45kg45kx0").encode() + b"\r\n")
        elif line.startswith("A1PMQA"):
          writer.write(echo(line, "rt" + " ".join(["0"] * 8)).encode() + b"\r\n")
        elif line.startswith("A1PMQW"):
          writer.write(echo(line, "qw1").encode() + b"\r\n")
        else:
          writer.write(echo(line).encode() + b"\r\n")
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
      pass
    finally:
      writer.close()

  async def test_setup_and_a_command_over_a_host(self):
    driver = VantageDriver(host="127.0.0.1", port=self.port, read_timeout=2)
    try:
      with self.assertNoLogs(ROUTER_LOGGER, "WARNING"):
        await within(driver.setup())
        self.assertIsInstance(driver.io, HamiltonTextTCP)
        reply = await within(driver.send_command("A1AM", "QW", fmt={"qw": "int"}))
      self.assertEqual(reply, {"qw": 1, "id": 7})
      self.assertEqual(
        self.received,
        [
          "A1PMQAid0001",
          "A1AMQUid0002",
          "A1AMQNid0003",
          'A1AMQCid0004cd"P"',
          "A1AMQWid0005",
          "A1PMQWid0006",
          "A1AMQWid0007",
        ],
      )
    finally:
      await driver.stop()
    self.assertFalse(driver.connected)

  async def test_the_arm_is_pre_initialized_over_tcp(self):
    self.arm_initialized = False
    driver = VantageDriver(host="127.0.0.1", port=self.port, read_timeout=2)
    try:
      await within(driver.setup())
      self.assertEqual(
        [line[:6] for line in self.received],
        ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1AMMI", "A1PMQW"],
      )
    finally:
      await driver.stop()

  async def test_events_name_the_tcp_transport(self):
    driver = VantageDriver(host="127.0.0.1", port=self.port, read_timeout=2)
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    try:
      with use_event_bus(bus):
        await within(driver.setup())
      self.assertEqual(events[0].data["transport"], "hamilton_text_tcp")
    finally:
      await driver.stop()

  async def test_setup_can_follow_a_stop(self):
    driver = VantageDriver(host="127.0.0.1", port=self.port, read_timeout=2)
    await within(driver.setup())
    await driver.stop()
    try:
      await within(driver.setup())
      self.assertEqual(
        [line[:6] for line in self.received],
        ["A1PMQA", "A1AMQU", "A1AMQN", "A1AMQC", "A1AMQW", "A1PMQW"] * 2,
      )
    finally:
      await driver.stop()


if __name__ == "__main__":
  unittest.main()


class TestTipTypes(unittest.IsolatedAsyncioTestCase):
  """The tip type table."""

  async def asyncSetUp(self):
    self.io = ScriptedIO(machine())
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()

  async def asyncTearDown(self):
    self.driver._router.stop()

  def tip(
    self,
    model: str = "a",
    has_filter: bool = False,
    maximal_volume: float = 400.0,
    tip_size: TipSize = TipSize.STANDARD_VOLUME,
  ) -> HamiltonTip:
    return HamiltonTip(
      model=None if model == "unset" else model,
      name="tip",
      has_filter=has_filter,
      size_z=59.9,
      nominal_volume=300,
      maximal_volume=maximal_volume,
      tip_size=tip_size,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
      collar_height=8.0,
      diameter=8.0,
    )

  def definitions(self) -> List[str]:
    """The tip type entries the driver wrote."""
    return [command for command in self.io.written if command.startswith("A1AMTT")]

  async def test_a_plain_300_ul_tip_uses_the_entry_the_machine_already_has(self):
    self.assertEqual(await self.driver.get_or_assign_tip_type_index(self.tip()), 0)
    self.assertEqual(self.definitions(), [])

  async def test_the_filtered_one_has_its_own_entry(self):
    self.assertEqual(
      await self.driver.get_or_assign_tip_type_index(self.tip("filtered", True, 360.0)), 1
    )
    self.assertEqual(self.definitions(), [])

  async def test_a_1000_ul_tip_uses_the_entry_the_machine_already_has(self):
    self.assertEqual(
      await self.driver.get_or_assign_tip_type_index(
        self.tip("big", False, 1250.0, TipSize.HIGH_VOLUME)
      ),
      4,
    )
    self.assertEqual(self.definitions(), [])

  async def test_the_same_entry_comes_back_again(self):
    first = await self.driver.get_or_assign_tip_type_index(self.tip())
    second = await self.driver.get_or_assign_tip_type_index(self.tip())
    self.assertEqual((first, second), (0, 0))

  async def test_a_tip_the_machine_does_not_have_is_written_above_its_own_entries(self):
    index = await self.driver.get_or_assign_tip_type_index(
      self.tip("odd", False, 250.0, TipSize.STANDARD_VOLUME)
    )
    self.assertEqual(index, 18)
    self.assertEqual(len(self.definitions()), 1)
    self.assertIn("ti18", self.definitions()[0])

  async def test_each_tip_the_machine_does_not_have_gets_the_next_entry(self):
    first = await self.driver.get_or_assign_tip_type_index(self.tip("odd", False, 250.0))
    second = await self.driver.get_or_assign_tip_type_index(self.tip("odder", False, 260.0))
    self.assertEqual((first, second), (18, 19))

  async def test_the_machines_own_entries_are_never_given_to_a_tip_it_does_not_have(self):
    for index in range(18, 100):
      self.driver._tip_type_indices[f"taken{index}"] = index
    with self.assertRaises(ValueError) as caught:
      await self.driver.get_or_assign_tip_type_index(self.tip("odd", False, 250.0))
    self.assertIn("full", str(caught.exception))

  async def test_the_grippers_entry_is_not_used_for_a_tip_that_looks_like_it(self):
    index = await self.driver.get_or_assign_tip_type_index(
      self.tip("probe", False, 1.0, TipSize.UNDEFINED)
    )
    self.assertEqual(index, 18)

  async def test_a_tip_without_a_model_is_refused(self):
    with self.assertRaises(ValueError):
      await self.driver.get_or_assign_tip_type_index(self.tip("unset"))

  async def test_the_table_is_written_off_when_the_link_closes(self):
    await self.driver.get_or_assign_tip_type_index(self.tip())
    await self.driver.stop()
    self.assertEqual(self.driver._tip_type_indices, {})
