import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union, cast

import anyio
import pytest

from pylabrobot.agilent.benchcel import (
  BenchCel4R,
  BenchCelLabwareSettings,
  PlateNotchSettings,
  Teachpoint,
  apply_benchcel_labware_settings,
  calculate_benchcel_labware_settings,
  calculate_robot_gripper_offset,
  calculate_sensor_offset,
  calculate_stacker_gripper_offset,
  calculate_stacking_thickness,
)
from pylabrobot.agilent.benchcel.benchcel import (
  BenchCelDeviceError,
  BenchCelProtocolError,
  Frame,
  parse_arm_pose_from_87_payload,
  parse_frame_from_buffer,
  parse_sensor_response,
  split_frames,
)
from pylabrobot.agilent.benchcel.stacks import benchcel_4r_stacks
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, PlateHolder, cor_96_wellplate_360uL_Fb
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.testing.concurrency import AnyioTestBase

TEST_TEACHPOINT = Teachpoint(
  theta=89.99874114990234,
  x=-360.8802795410156,
  z=-10.0,
  approach_height=20.0,
  cavity_depth=0.0,
  gripper_open_limit=-1.5,
  respect_approach_height_when_not_holding_plate=True,
  something_above_this_point=False,
  teachpoint_id=0x1F,
  name="test",
)


ReadEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]


class _FakeSocket(HasLifespan):
  """Native fake Socket lifecycle with queued reads and captured writes."""

  def __init__(
    self,
    chunks: list[ReadEffect],
    *,
    events: Optional[list[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    retain_cancel_scope: bool = False,
  ) -> None:
    super().__init__()
    self.sent = bytearray()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.retain_cancel_scope = retain_cancel_scope
    self.setup_calls = 0
    self.stop_calls = 0
    self.reconnect_calls = 0
    self._chunks = list(chunks)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("_FakeSocket does not accept lifespan options")
    self.setup_calls += 1
    self.events.append("socket.enter")
    stack.push_shielded_async_callback(self._close)
    if self.retain_cancel_scope:
      stack.enter_context(anyio.CancelScope())
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.stop_calls += 1
    self.events.append("socket.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reconnect(self) -> None:
    if not self.is_active:
      raise RuntimeError("reconnect outside socket lifespan")
    self.reconnect_calls += 1
    self.events.append("socket.reconnect")
    await anyio.lowlevel.checkpoint()

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    await anyio.lowlevel.checkpoint()
    self.sent.extend(data)
    self.events.append(f"write:{data.hex()}")

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._chunks:
      return b""
    effect = self._chunks.pop(0)
    if isinstance(effect, BaseException):
      raise effect
    chunk = await effect() if callable(effect) else effect
    if len(chunk) > num_bytes:
      self._chunks.insert(0, chunk[num_bytes:])
      return chunk[:num_bytes]
    return chunk


class _SlowSocket(_FakeSocket):
  """Socket double whose entry never completes without cancellation."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    await anyio.sleep_forever()


class _SlowFirstReconnectSocket(_FakeSocket):
  """Socket double whose first reconnect times out before a successful retry."""

  async def reconnect(self) -> None:
    await super().reconnect()
    if self.reconnect_calls == 1:
      await anyio.sleep_forever()


def _make_backend(
  chunks: list[ReadEffect],
  *,
  events: Optional[list[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  stacks: Optional[list[ResourceStack]] = None,
) -> tuple[BenchCel4R, _FakeSocket]:
  backend = BenchCel4R(
    name="benchcel",
    host="ignored",
    port=0,
    timeout=1.0,
    read_poll_timeout=0.01,
    stacks=stacks,
  )
  socket = _FakeSocket(chunks, events=events, entry_error=entry_error, exit_error=exit_error)
  backend.io = cast(Socket, socket)
  return backend, socket


def _sensor_payload(stacker_index: int = 2) -> bytes:
  return struct.pack(
    "<BB8H",
    stacker_index,
    0x08,
    45,
    1,
    0,
    240,
    128,
    241,
    0,
    1,
  )


class _AnyioTestCase(AnyioTestBase):
  """AnyIO test base retaining unittest assertion helpers used by this module."""

  def assertAlmostEqual(self, *args: Any, **kwargs: Any) -> None:
    self._assertions.assertAlmostEqual(*args, **kwargs)

  def assertEqual(self, *args: Any, **kwargs: Any) -> None:
    self._assertions.assertEqual(*args, **kwargs)

  def assertIs(self, *args: Any, **kwargs: Any) -> None:
    self._assertions.assertIs(*args, **kwargs)

  def assertIsNone(self, *args: Any, **kwargs: Any) -> None:
    self._assertions.assertIsNone(*args, **kwargs)

  def assertNotIn(self, *args: Any, **kwargs: Any) -> None:
    self._assertions.assertNotIn(*args, **kwargs)

  def assertRaises(self, *args: Any, **kwargs: Any) -> Any:
    return self._assertions.assertRaises(*args, **kwargs)

  def assertRaisesRegex(self, *args: Any, **kwargs: Any) -> Any:
    return self._assertions.assertRaisesRegex(*args, **kwargs)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self._assertions = unittest.TestCase()


class BenchCelFrameTests(unittest.TestCase):
  def test_frame_hex(self):
    self.assertEqual(Frame(0x48, b"\x01").hex(), "48010001")
    self.assertEqual(Frame(0x6A, b"\x01").hex(), "6a010001")

  def test_parse_frame_from_buffer_and_split(self):
    data = bytes.fromhex("6901004869010065")
    frames = split_frames(data)
    self.assertEqual(frames, [Frame(0x69, b"\x48"), Frame(0x69, b"\x65")])

    buffer = bytearray(bytes.fromhex("690100"))
    self.assertIsNone(parse_frame_from_buffer(buffer))
    buffer.extend(b"\x48")
    self.assertEqual(parse_frame_from_buffer(buffer), Frame(0x69, b"\x48"))
    self.assertEqual(buffer, bytearray())

  def test_split_frames_rejects_partial_trailer(self):
    with self.assertRaises(BenchCelProtocolError):
      split_frames(bytes.fromhex("69010048ff02"))


class BenchCelParserTests(unittest.TestCase):
  def test_parse_sensor_response(self):
    status = parse_sensor_response(Frame(0x7E, _sensor_payload(stacker_index=2)))
    self.assertEqual(status.stacker, 3)
    self.assertEqual(status.air_pressure, 45)
    self.assertEqual(status.plate_presence, 128)
    self.assertTrue(status.plate_present())
    self.assertEqual(status.notch_values(), (1, 0, 0, 1))

  def test_parse_arm_pose(self):
    payload = bytearray(66)
    struct.pack_into("<f", payload, 4, 12.5)
    struct.pack_into("<f", payload, 12, -34.25)
    struct.pack_into("<f", payload, 20, 99.0)
    struct.pack_into("<f", payload, 28, -1.0)
    pose = parse_arm_pose_from_87_payload(bytes(payload))
    self.assertAlmostEqual(pose.rotation.z, 12.5)
    self.assertAlmostEqual(pose.location.x, -34.25)
    self.assertAlmostEqual(pose.location.z, 99.0)
    self.assertAlmostEqual(pose.gripper_position, -1.0)


class BenchCelLabwareSettingsTests(unittest.TestCase):
  def test_calculated_labware_maps_gripper_offset_to_pickup_location(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=10.4, ordered_items={})
    settings = calculate_benchcel_labware_settings(plate, name="low-profile")
    self.assertEqual(settings.name, "low-profile")
    self.assertAlmostEqual(settings.stacking_thickness, 8.9)
    self.assertAlmostEqual(settings.effective_plate_height(), 10.4)
    self.assertAlmostEqual(settings.robot_gripper_offset, 5.0)
    self.assertAlmostEqual(settings.robot_pickup_distance_from_top(), 5.4)
    self.assertAlmostEqual(settings.stacker_gripper_offset, 4.0)
    self.assertAlmostEqual(settings.sensor_offset, 7.0)

    returned = apply_benchcel_labware_settings(plate, settings)
    self.assertEqual(returned, settings)
    self.assertEqual(plate.preferred_pickup_location, Coordinate(63.88, 42.74, 5.0))

  def test_calculation_helpers_match_observed_example_classes(self):
    self.assertAlmostEqual(calculate_stacking_thickness(14.6), 13.1)
    self.assertAlmostEqual(calculate_stacking_thickness(44.04), 42.54)
    self.assertAlmostEqual(calculate_stacking_thickness(10.4, nesting_overlap=1.3), 9.1)
    self.assertAlmostEqual(calculate_robot_gripper_offset(14.6), 8.0)
    self.assertAlmostEqual(calculate_robot_gripper_offset(10.4), 5.0)
    self.assertAlmostEqual(calculate_stacker_gripper_offset(10.4, 5.0), 4.0)
    self.assertAlmostEqual(calculate_stacker_gripper_offset(14.6, 8.0), 5.0)
    self.assertAlmostEqual(calculate_stacker_gripper_offset(44.04, 8.0), 6.0)
    self.assertAlmostEqual(calculate_sensor_offset(10.4), 7.0)
    self.assertAlmostEqual(calculate_sensor_offset(14.6), 8.0)
    self.assertAlmostEqual(calculate_sensor_offset(44.04), 40.04)

  def test_stacking_thickness_estimated_from_height(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={})
    settings = calculate_benchcel_labware_settings(plate)
    self.assertAlmostEqual(settings.stacking_thickness, calculate_stacking_thickness(14.6))

  def test_explicit_stacking_thickness_overrides_estimate(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={})
    settings = calculate_benchcel_labware_settings(plate, stacking_thickness=11.0)
    self.assertAlmostEqual(settings.stacking_thickness, 11.0)

  def test_labware_profile_validates_plate_height(self):
    source = Plate("plate", size_x=127.76, size_y=85.48, size_z=10.4, ordered_items={})
    settings = calculate_benchcel_labware_settings(source)
    wrong_height = Plate("plate", size_x=127.76, size_y=85.48, size_z=5.4, ordered_items={})
    with self.assertRaisesRegex(ValueError, "expected 10.400 mm"):
      settings.apply_to_plate(wrong_height)

  def test_device_payload_matches_vworks_capture(self):
    # Greiner 781101 settings captured from VWorks (0x7d payload).
    settings = BenchCelLabwareSettings(
      name="781101",
      plate_size_x=127.76,
      plate_size_y=85.48,
      plate_size_z=14.4,
      stacking_thickness=12.76,
      robot_gripper_offset=8.0,
      stacker_gripper_offset=5.0,
      sensor_offset=8.0,
      gripper_open_position=-1.0,
      gripper_holding_plate_position=8.0,
      gripper_holding_stack_position=8.5,
      orientation_sensor_threshold=100,
      sensor_intensity=50,
      plate_presence_threshold=225,
      notch_settings=PlateNotchSettings(
        check_orientation=True,
        a1_notch=True,
        top_right_notch=False,
        bottom_left_notch=True,
        bottom_right_notch=False,
      ),
    )
    expected = (
      "f6284c41000000410000a040000000410100010064003200000080bf00000041000008410166"
      "66664100000000000000000000000000000000000000000000000000000000010000000000e100"
    )
    self.assertEqual(settings.to_device_payload().hex(), expected)
    self.assertEqual(len(settings.to_device_payload()), 77)

  def test_device_payload_round_trip(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=14.4, ordered_items={})
    settings = calculate_benchcel_labware_settings(plate, name="rt")
    decoded = BenchCelLabwareSettings.from_device_payload(settings.to_device_payload())
    self.assertAlmostEqual(decoded.stacking_thickness, settings.stacking_thickness, places=3)
    self.assertAlmostEqual(decoded.plate_size_z, settings.plate_size_z, places=3)
    self.assertAlmostEqual(decoded.robot_gripper_offset, settings.robot_gripper_offset, places=3)
    self.assertEqual(decoded.plate_presence_threshold, settings.plate_presence_threshold)
    self.assertEqual(decoded.notch_settings.a1_notch, settings.notch_settings.a1_notch)

  def test_benchcel_4r_stacks_are_lifo_resource_stacks(self):
    stacks = benchcel_4r_stacks()
    self.assertEqual(len(stacks), 4)
    self.assertTrue(all(isinstance(s, ResourceStack) for s in stacks))
    self.assertTrue(all(s.direction == "z" for s in stacks))
    # Plates can be tracked on a stack (LIFO): the last one added is the accessible (top) plate.
    for name in ("p0", "p1"):
      stacks[0].assign_child_resource(
        Plate(name, size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={})
      )
    self.assertEqual(len(stacks[0].children), 2)
    self.assertEqual(stacks[0].get_top_item().name, "p1")

  def test_parse_labware_xml(self):
    xml = """<?xml version='1.0' encoding='ASCII'?>
<Velocity11 file='BenchCel ActiveX Settings'>
  <Labware>
    <Name>Example</Name>
    <StackingThickness>12.34</StackingThickness>
    <OrientationSensorThreshold>100</OrientationSensorThreshold>
    <PlatePresenceThreshold>225</PlatePresenceThreshold>
    <SensorIntensity>50</SensorIntensity>
    <ErrorCorrectionOffset>0.0</ErrorCorrectionOffset>
    <RobotGripperOffset>8.0</RobotGripperOffset>
    <StackerGripperOffset>5.0</StackerGripperOffset>
    <SensorOffset>8.0</SensorOffset>
    <GripperOpenPosition>-1.0</GripperOpenPosition>
    <GripperHoldingPlatePosition>8.0</GripperHoldingPlatePosition>
    <GripperHoldingStackPosition>8.5</GripperHoldingStackPosition>
    <PlateNotchesOrientationOptions>
      <CheckOrientation>Yes</CheckOrientation>
      <A1Notch>Yes</A1Notch>
      <TopRightNotch>No</TopRightNotch>
      <BottomLeftNotch>Yes</BottomLeftNotch>
      <BottomRightNotch>No</BottomRightNotch>
    </PlateNotchesOrientationOptions>
    <CanBeLidded>No</CanBeLidded>
    <CanBeSealed>No</CanBeSealed>
  </Labware>
  <StackSettings>
    <PlatePresenceThreshold>50</PlatePresenceThreshold>
    <RackPresenceThreshold>3</RackPresenceThreshold>
    <AdditionalReleaseHeight>2.0</AdditionalReleaseHeight>
    <LowPressureWarning>30</LowPressureWarning>
    <TiltMargin Enabled='No'>2.0</TiltMargin>
  </StackSettings>
</Velocity11>
"""
    with tempfile.TemporaryDirectory() as td:
      path = Path(td) / "example.xml"
      path.write_text(xml, encoding="ascii")
      settings = BenchCelLabwareSettings.from_xml_file(
        path,
        identifier="example",
        plate_size_x=127.76,
        plate_size_y=85.48,
        plate_size_z=14.4,
      )
    self.assertEqual(settings.identifier, "example")
    self.assertEqual(settings.name, "Example")
    self.assertAlmostEqual(settings.stacking_thickness, 12.34)
    self.assertEqual(settings.stack_plate_presence_threshold, 50)
    self.assertTrue(settings.notch_settings.a1_notch)


class BenchCelFactoryTests(unittest.TestCase):
  def test_factory_creates_device_with_four_stacks(self):
    benchcel = BenchCel4R(name="bc", host="192.168.0.100")
    self.assertIsInstance(benchcel, BenchCel4R)
    self.assertEqual(benchcel.host, "192.168.0.100")
    self.assertEqual(len(benchcel.stacks), 4)
    self.assertTrue(all(isinstance(s, ResourceStack) for s in benchcel.stacks))
    self.assertEqual(benchcel.model, "Agilent BenchCel 4R")

  def test_factory_records_labware_settings(self):
    plate = Plate("plate", size_x=127.76, size_y=85.47, size_z=44.04, ordered_items={})
    benchcel = BenchCel4R(name="bc", host="192.168.0.100", labware=plate)
    assert benchcel.labware_settings is not None
    self.assertEqual(benchcel.labware_settings.name, "plate")
    # no stacking_z_height on the plate -> estimated pitch (44.04 - 1.5)
    self.assertAlmostEqual(benchcel.labware_settings.stacking_thickness, 42.54)


class TestBenchCelWire(_AnyioTestCase):
  async def test_split_frame_uses_caller_deadline_without_per_read_timeout(self) -> None:
    """A delayed frame can span chunks without passing a transport timeout."""
    backend, socket = _make_backend([b"\x69", b"\x01\x00\x48"])
    original_read = socket.read

    async def read_without_timeout(num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
      assert timeout is None
      return await original_read(num_bytes)

    socket.read = read_without_timeout  # type: ignore[method-assign]
    assert await backend._read_frame(timeout=0.1) == Frame(0x69, b"\x48")

  async def test_lower_level_home_command_writes_expected_frame(self):
    backend, writer = _make_backend([b"\x69", b"\x01\x00\x48"])
    ack = await backend._send_home_command()
    self.assertEqual(writer.sent.hex(), "48010001")
    self.assertEqual(ack, Frame(0x69, b"\x48"))

  async def test_home_reconnects_and_confirms_status(self):
    events: list[str] = []
    backend, socket = _make_backend(
      [Frame(0x69, b"\x47").to_bytes(), Frame(0x87).to_bytes()], events=events
    )

    async with backend:
      await backend.home()
      events.append("body")

    assert socket.sent.hex() == "47010001870000"
    assert socket.setup_calls == 1
    assert socket.stop_calls == 1
    assert socket.reconnect_calls == 1
    assert events == [
      "socket.enter",
      "write:47010001",
      "socket.reconnect",
      "write:870000",
      "body",
      "socket.exit",
    ]

  async def test_home_reconnects_with_socket_cancel_scope(self) -> None:
    backend, _ = _make_backend([])
    socket = _FakeSocket(
      [Frame(0x69, b"\x47").to_bytes(), Frame(0x87).to_bytes()],
      retain_cancel_scope=True,
    )
    backend.io = cast(Socket, socket)

    async with backend:
      await backend.home()

    assert socket.setup_calls == 1
    assert socket.stop_calls == 1
    assert socket.reconnect_calls == 1

  async def test_home_retries_after_reconnect_timeout_with_socket_cancel_scope(self) -> None:
    backend, _ = _make_backend([])
    backend.timeout = 0.01
    socket = _SlowFirstReconnectSocket(
      [Frame(0x69, b"\x47").to_bytes(), Frame(0x87).to_bytes()],
      retain_cancel_scope=True,
    )
    backend.io = cast(Socket, socket)

    async with backend:
      await backend.home(timeout=4.0)

    assert socket.setup_calls == 1
    assert socket.stop_calls == 1
    assert socket.reconnect_calls == 2

  async def test_home_timeout_interrupts_stalled_status_poll(self) -> None:
    async def stalled_status() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, socket = _make_backend([Frame(0x69, b"\x47").to_bytes(), stalled_status])
    async with backend:
      with anyio.fail_after(0.3):
        with pytest.raises(TimeoutError, match="home-motors: device not responsive"):
          await backend.home(timeout=0.02)

    assert socket.reconnect_calls == 1
    assert socket.stop_calls == 1

  async def test_move_to_stacker_writes_expected_frame(self):
    backend, writer = _make_backend([Frame(0x69, b"\x65").to_bytes()])
    await backend.move_to_stacker(backend.stacks[2])
    self.assertEqual(writer.sent.hex(), "650a0001020000204100000000")

  async def test_set_teachpoint_writes_captured_shape(self):
    backend, writer = _make_backend([])
    await backend.set_teachpoint(TEST_TEACHPOINT)
    self.assertEqual(
      writer.sent.hex(),
      "731b001f5bffb342ad70b4c3000020c100010000a041000000000000c0bf",
    )

  async def test_device_error_raises(self):
    payload = b"X position out of bounds"
    frame = Frame(0x02, payload).to_bytes()
    backend, writer = _make_backend([frame])
    with self.assertRaises(BenchCelDeviceError) as cm:
      await backend._move_x_relative(500)
    self.assertEqual(writer.sent.hex(), Frame(0x66, struct.pack("<Bf", 1, 500.0)).hex())
    self.assertEqual(cm.exception.message, "X position out of bounds")

  async def test_request_stacker_sensor_status(self):
    response = Frame(0x7E, _sensor_payload(stacker_index=2)).to_bytes()
    backend, writer = _make_backend([response])
    status = await backend.request_stacker_sensor_status(backend.stacks[2])
    self.assertEqual(writer.sent.hex(), "7e010002")
    self.assertEqual(status.stacker, 3)
    self.assertEqual(status.plate_presence, 128)

  async def test_request_axis_limits(self):
    payload = struct.pack("<8f", -115.0, -360.9, -1.5, -1.5, 115.0, 360.9, 104.0, 11.0)
    backend, writer = _make_backend([Frame(0x99, payload).to_bytes()])
    limits = await backend.request_axis_limits()
    self.assertEqual(writer.sent.hex(), "990000")
    self.assertAlmostEqual(limits["theta"][0], -115.0)
    self.assertAlmostEqual(limits["x"][1], 360.9, places=3)
    self.assertAlmostEqual(limits["gripper"][1], 11.0)

  async def test_open_and_close_stacker_clamps(self):
    backend, writer = _make_backend(
      [
        Frame(0x69, b"\x67").to_bytes(),
        Frame(0x69, b"\x67").to_bytes(),
      ]
    )
    await backend.open_stacker_clamps(backend.stacks[0])
    await backend.close_stacker_clamps(backend.stacks[0])
    self.assertEqual(writer.sent.hex(), "67020000016702000000")

  async def test_stacker_primitives_match_vworks_captures(self):
    # Wire bytes confirmed against VWorks packet captures of the Downstack,
    # Upstack, Load, and Unload buttons on stacker 3 (zero-based index 0x02):
    #   Downstack -> 0x62  01 02 00 01            ACK 0x69 62
    #   Upstack   -> 0x63  01 02 00 01            ACK 0x69 63
    #   Load      -> 0x60  01 02                  ACK 0x69 60 02
    #   Unload    -> 0x61  01 02 00 00 00 00      ACK 0x69 61 02
    backend, writer = _make_backend(
      [
        Frame(0x69, b"\x62").to_bytes(),
        Frame(0x69, b"\x63").to_bytes(),
        Frame(0x69, b"\x60\x02").to_bytes(),
        Frame(0x69, b"\x61\x02").to_bytes(),
      ]
    )
    await backend._pick_from_stacker(3)
    await backend._place_on_stacker(3)
    await backend._load_stacker(3)
    await backend._unload_stacker(3)
    self.assertEqual(
      writer.sent.hex(),
      "62040001020001"  # downstack stacker 3
      "63040001020001"  # upstack stacker 3
      "6002000102"  # load stacker 3
      "610600010200000000",  # unload stacker 3
    )


class TestBenchCelLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert BenchCel4R.__aenter__ is HasLifespan.__aenter__
    assert BenchCel4R.__aexit__ is HasLifespan.__aexit__
    assert BenchCel4R.setup is HasLifespan.setup
    assert BenchCel4R.stop is HasLifespan.stop

  async def test_lifespan_rejects_options_before_opening_socket(self) -> None:
    backend, socket = _make_backend([])

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(home=True):
        pytest.fail("invalid lifespan options reached body")

    assert socket.setup_calls == 0
    assert not backend.is_active

  async def test_socket_entry_failure_rolls_back(self) -> None:
    failure = RuntimeError("socket entry failed")
    backend, socket = _make_backend([], entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed socket entry reached body")

    assert error_info.value is failure
    assert socket.setup_calls == 1
    assert socket.stop_calls == 1
    assert not backend.is_active

  async def test_entry_timeout_scope_outlives_socket_cancel_scope(self) -> None:
    backend, _ = _make_backend([])
    socket = _FakeSocket([], retain_cancel_scope=True)
    backend.io = cast(Socket, socket)

    async with backend:
      assert socket.is_active

    assert socket.stop_calls == 1
    assert not backend.is_active

  async def test_socket_entry_timeout_closes_socket(self) -> None:
    backend, _ = _make_backend([])
    backend.timeout = 0.01
    socket = _SlowSocket([], retain_cancel_scope=True)
    backend.io = cast(Socket, socket)

    with pytest.raises(TimeoutError):
      async with backend:
        pytest.fail("timed-out socket entry reached body")

    assert socket.stop_calls == 1
    assert not backend.is_active

  async def test_body_cancellation_closes_socket_before_propagating(self) -> None:
    events: list[str] = []
    backend, _ = _make_backend([], events=events)

    async def use_backend() -> None:
      try:
        async with backend:
          events.append("body")
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      while "body" not in events:
        await anyio.lowlevel.checkpoint()
      task_group.cancel_scope.cancel()

    assert events == ["socket.enter", "body", "socket.exit", "cancelled"]
    assert not backend.is_active

  async def test_sequential_reuse_opens_and_closes_socket_twice(self) -> None:
    backend, socket = _make_backend([])

    async with backend:
      assert backend.is_active
    async with backend:
      assert backend.is_active

    assert socket.setup_calls == 2
    assert socket.stop_calls == 2
    assert not backend.is_active

  async def test_socket_close_failure_propagates_after_deactivation(self) -> None:
    failure = RuntimeError("socket close failed")
    backend, socket = _make_backend([], exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert socket.stop_calls == 1
    assert not backend.is_active


def _make_transfer(
  operation: str,
  blocked_command: int,
) -> tuple[
  BenchCel4R,
  _FakeSocket,
  Plate,
  ResourceStack | PlateHolder,
  ResourceStack | PlateHolder,
  anyio.Event,
  anyio.Event,
]:
  stacks = benchcel_4r_stacks()
  started = anyio.Event()
  release = anyio.Event()

  async def blocked_ack() -> bytes:
    started.set()
    await release.wait()
    return Frame(0x69, bytes([blocked_command])).to_bytes()

  effects: list[ReadEffect] = [Frame(0x69, b"\x6a").to_bytes()]
  effects.append(blocked_ack if blocked_command == 0x62 else Frame(0x69, b"\x62").to_bytes())
  if blocked_command == 0x63:
    effects.append(blocked_ack)
  backend, socket = _make_backend(effects, stacks=stacks)
  backend.loading_tray_teachpoint_id = 0x1E
  plate = cor_96_wellplate_360uL_Fb("plate")
  source: ResourceStack | PlateHolder
  destination: ResourceStack | PlateHolder
  if operation == "upstack":
    source = backend.loading_tray
    destination = stacks[0]
    backend.loading_tray.assign_child_resource(plate)
  else:
    source = stacks[0]
    destination = backend.loading_tray if operation == "downstack" else stacks[1]
    stacks[0].assign_child_resource(plate)
  return backend, socket, plate, source, destination, started, release


async def _run_transfer(backend: BenchCel4R, operation: str) -> None:
  if operation == "downstack":
    await backend.downstack(backend.stacks[0])
  elif operation == "upstack":
    await backend.upstack(backend.stacks[0])
  else:
    await backend.move_plate_between_stacks(backend.stacks[0], backend.stacks[1])


class TestBenchCelTransferCancellation(AnyioTestBase):
  @pytest.mark.parametrize("operation", ["downstack", "upstack", "move"])
  async def test_cancellation_during_pick_reconciles_ack_without_model_commit(
    self, operation: str
  ) -> None:
    backend, socket, plate, source, destination, started, release = _make_transfer(operation, 0x62)
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def transfer() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await _run_transfer(backend, operation)
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(transfer)
      await started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not cancelled.is_set()
      release.set()

    assert plate.parent is source
    assert plate.parent is not destination
    assert split_frames(bytes(socket.sent)) == [
      Frame(0x6A, b"\x01"),
      Frame(0x62, b"\x01" + (b"\x1e" if operation == "upstack" else b"\x00") + b"\x00\x01"),
    ]

  @pytest.mark.parametrize("operation", ["downstack", "upstack", "move"])
  async def test_cancellation_during_place_reconciles_ack_then_commits_model(
    self, operation: str
  ) -> None:
    backend, socket, plate, source, destination, started, release = _make_transfer(operation, 0x63)
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def transfer() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await _run_transfer(backend, operation)
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(transfer)
      await started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not cancelled.is_set()
      assert plate.parent is source
      release.set()

    assert cancelled.is_set()
    assert plate.parent is destination
    assert [frame.command_id for frame in split_frames(bytes(socket.sent))] == [0x6A, 0x62, 0x63]

  @pytest.mark.parametrize("operation", ["downstack", "upstack", "move"])
  async def test_model_commit_waits_for_place_terminal_success(self, operation: str) -> None:
    backend, _, plate, source, destination, started, release = _make_transfer(operation, 0x63)
    completed = anyio.Event()

    async def transfer() -> None:
      await _run_transfer(backend, operation)
      completed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(transfer)
      await started.wait()
      assert plate.parent is source
      assert plate.parent is not destination
      assert not completed.is_set()
      release.set()

    assert plate.parent is destination


class TestBenchCelStackerMapping(_AnyioTestCase):
  async def test_transfers_require_a_configured_teachpoint(self):
    stacks = benchcel_4r_stacks()
    # No loading_tray_teachpoint_id.
    backend = BenchCel4R(name="benchcel", host="ignored", stacks=stacks)
    self.assertIsNone(backend.loading_tray_teachpoint_id)

    with self.assertRaisesRegex(ValueError, "teachpoint"):
      await backend.downstack(stacks[0])
    with self.assertRaisesRegex(ValueError, "teachpoint"):
      await backend.upstack(stacks[0])

  async def test_downstack_maps_stack_to_stacker(self):
    stacks = benchcel_4r_stacks()
    backend = BenchCel4R(
      name="benchcel",
      host="ignored",
      stacks=stacks,
      loading_tray_teachpoint_id=0x1E,
    )
    plate = cor_96_wellplate_360uL_Fb("plate")
    stacks[2].assign_child_resource(plate)

    sent: list[Frame] = []

    async def fake_send(frame: Frame, **kwargs) -> Frame:
      sent.append(frame)
      if kwargs.get("on_success") is not None:
        kwargs["on_success"]()
      return Frame(0x69, kwargs.get("ack_payload") or bytes([frame.command_id]))

    backend._send_frame_expect_ack_no_lock = fake_send  # type: ignore[method-assign]
    # stacks[2] is human stacker 3 (zero-based index 0x02)
    returned = await backend.downstack(stacks[2])

    self.assertEqual(
      [f.hex() for f in sent],
      ["6a010001", "62040001020001", "630400011e0001"],
    )
    self.assertIs(returned, plate)
    self.assertIs(backend.loading_tray.resource, plate)
    self.assertNotIn(plate, stacks[2].children)

  async def test_upstack_maps_stack_to_stacker(self):
    stacks = benchcel_4r_stacks()
    backend = BenchCel4R(
      name="benchcel",
      host="ignored",
      stacks=stacks,
      loading_tray_teachpoint_id=0x1E,
    )
    plate = cor_96_wellplate_360uL_Fb("plate")
    backend.loading_tray.assign_child_resource(plate)

    sent: list[Frame] = []

    async def fake_send(frame: Frame, **kwargs) -> Frame:
      sent.append(frame)
      if kwargs.get("on_success") is not None:
        kwargs["on_success"]()
      return Frame(0x69, kwargs.get("ack_payload") or bytes([frame.command_id]))

    backend._send_frame_expect_ack_no_lock = fake_send  # type: ignore[method-assign]
    # stacks[0] is human stacker 1 (zero-based index 0x00)
    await backend.upstack(stacks[0])

    self.assertEqual(
      [f.hex() for f in sent],
      ["6a010001", "620400011e0001", "63040001000001"],
    )
    self.assertIsNone(backend.loading_tray.resource)
    self.assertIs(stacks[0].get_top_item(), plate)

  async def test_transfer_rejects_foreign_stack_with_same_name(self):
    backend = BenchCel4R(name="benchcel", host="ignored", loading_tray_teachpoint_id=0x1E)
    foreign_stack = ResourceStack(name=backend.stacks[0].name, direction="z")

    with self.assertRaisesRegex(ValueError, "not configured"):
      await backend.downstack(foreign_stack)

  async def test_move_plate_between_stacks_updates_resource_state(self):
    stacks = benchcel_4r_stacks()
    backend = BenchCel4R(name="benchcel", host="ignored", stacks=stacks)
    plate = cor_96_wellplate_360uL_Fb("plate")
    stacks[0].assign_child_resource(plate)
    sent: list[Frame] = []

    async def fake_send(frame: Frame, **kwargs) -> Frame:
      sent.append(frame)
      if kwargs.get("on_success") is not None:
        kwargs["on_success"]()
      return Frame(0x69, kwargs.get("ack_payload") or bytes([frame.command_id]))

    backend._send_frame_expect_ack_no_lock = fake_send  # type: ignore[method-assign]

    returned = await backend.move_plate_between_stacks(stacks[0], stacks[3])

    self.assertIs(returned, plate)
    self.assertNotIn(plate, stacks[0].children)
    self.assertIs(stacks[3].get_top_item(), plate)
    self.assertEqual(
      [frame.hex() for frame in sent],
      ["6a010001", "62040001000001", "63040001030001"],
    )


if __name__ == "__main__":
  unittest.main()
