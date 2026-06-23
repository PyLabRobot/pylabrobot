import asyncio
import struct
import tempfile
import unittest
from pathlib import Path

from pylabrobot.resources import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.storage.agilent import (
  BenchCel4R,
  BenchCelLabwareSettings,
  PlateNotchSettings,
  apply_benchcel_labware_settings,
  calculate_benchcel_labware_settings,
  calculate_robot_gripper_offset,
  calculate_sensor_offset,
  calculate_stacker_gripper_offset,
  calculate_stacking_thickness,
)
from pylabrobot.storage.agilent.benchcel_backend import (
  TEST_LEFT_TEACHPOINT,
  BenchCel4RBackend,
  BenchCelDeviceError,
  BenchCelProtocolError,
  Frame,
  parse_arm_status_from_87_payload,
  parse_frame_from_buffer,
  parse_sensor_response,
  split_frames,
)
from pylabrobot.storage.agilent.stacks import benchcel_4r_stacks
from pylabrobot.storage.stacker import Stacker
from pylabrobot.storage.stacker_backend import StackerBackend


class _FakeWriter:
  def __init__(self) -> None:
    self.sent = bytearray()
    self.closed = False

  def write(self, data: bytes) -> None:
    self.sent.extend(data)

  async def drain(self) -> None:
    return None

  def close(self) -> None:
    self.closed = True

  async def wait_closed(self) -> None:
    return None


class _FakeReader:
  def __init__(self, chunks: list[bytes]) -> None:
    self._chunks = list(chunks)

  async def read(self, num_bytes: int) -> bytes:
    if not self._chunks:
      return b""
    chunk = self._chunks.pop(0)
    if len(chunk) > num_bytes:
      self._chunks.insert(0, chunk[num_bytes:])
      return chunk[:num_bytes]
    return chunk


def _make_backend(chunks: list[bytes]) -> tuple[BenchCel4RBackend, _FakeWriter]:
  backend = BenchCel4RBackend(host="ignored", port=0, timeout=1.0, read_poll_timeout=0.01)
  writer = _FakeWriter()
  backend.io._writer = writer  # type: ignore[assignment]
  backend.io._reader = _FakeReader(chunks)  # type: ignore[assignment]
  return backend, writer


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

  def test_parse_arm_status(self):
    payload = bytearray(66)
    struct.pack_into("<f", payload, 4, 12.5)
    struct.pack_into("<f", payload, 12, -34.25)
    struct.pack_into("<f", payload, 20, 99.0)
    struct.pack_into("<f", payload, 28, -1.0)
    status = parse_arm_status_from_87_payload(bytes(payload))
    self.assertAlmostEqual(status.theta, 12.5)
    self.assertAlmostEqual(status.x, -34.25)
    self.assertAlmostEqual(status.z, 99.0)
    self.assertAlmostEqual(status.gripper, -1.0)


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

  def test_stacking_thickness_prefers_plate_stacking_z_height(self):
    # A plate that declares its own stacking pitch should drive StackingThickness directly,
    # instead of the height-based estimate.
    plate = Plate(
      "plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={}, stacking_z_height=12.9
    )
    settings = calculate_benchcel_labware_settings(plate)
    self.assertAlmostEqual(settings.stacking_thickness, 12.9)
    # sanity: this differs from the pure height-based estimate (14.6 - 1.5 = 13.1)
    self.assertNotAlmostEqual(settings.stacking_thickness, calculate_stacking_thickness(14.6))

  def test_stacking_thickness_falls_back_to_calculation_without_plate_value(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={})
    self.assertIsNone(plate.stacking_z_height)
    settings = calculate_benchcel_labware_settings(plate)
    self.assertAlmostEqual(settings.stacking_thickness, calculate_stacking_thickness(14.6))

  def test_explicit_stacking_thickness_overrides_plate_value(self):
    plate = Plate(
      "plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={}, stacking_z_height=12.9
    )
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
    # Stack height follows the plates' stacking_z_height once plates are added.
    stacks[0].assign_child_resource(
      Plate(
        "p0", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={}, stacking_z_height=13.1
      )
    )
    stacks[0].assign_child_resource(
      Plate(
        "p1", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={}, stacking_z_height=13.1
      )
    )
    self.assertAlmostEqual(stacks[0].get_size_z(), 13.1 + 14.6)

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
  def setUp(self):
    # Constructing the backend creates ``asyncio.Lock``/``Socket`` objects, which on Python 3.9
    # bind to the current event loop at init time; ensure one exists for these synchronous tests.
    self._loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self._loop)

  def tearDown(self):
    asyncio.set_event_loop(None)
    self._loop.close()

  def test_factory_creates_stacker_with_backend_and_four_stacks(self):
    benchcel = BenchCel4R(name="bc", host="192.168.0.100")
    self.assertIsInstance(benchcel, Stacker)
    backend = benchcel.backend
    assert isinstance(backend, BenchCel4RBackend)
    self.assertEqual(backend.host, "192.168.0.100")
    self.assertEqual(len(benchcel.stacks), 4)
    self.assertTrue(all(isinstance(s, ResourceStack) for s in benchcel.stacks))
    self.assertEqual(benchcel.model, "Agilent BenchCel 4R")

  def test_factory_records_labware_settings(self):
    plate = Plate("plate", size_x=127.76, size_y=85.47, size_z=44.04, ordered_items={})
    benchcel = BenchCel4R(name="bc", host="192.168.0.100", labware=plate)
    backend = benchcel.backend
    assert isinstance(backend, BenchCel4RBackend)
    assert backend.labware_settings is not None
    self.assertEqual(backend.labware_settings.name, "plate")
    # no stacking_z_height on the plate -> estimated pitch (44.04 - 1.5)
    self.assertAlmostEqual(backend.labware_settings.stacking_thickness, 42.54)


class BenchCelBackendWireTests(unittest.IsolatedAsyncioTestCase):
  async def test_home_writes_command_and_waits_for_split_ack(self):
    backend, writer = _make_backend([b"\x69", b"\x01\x00\x48"])
    ack = await backend.home()
    self.assertEqual(writer.sent.hex(), "48010001")
    self.assertEqual(ack, Frame(0x69, b"\x48"))

  async def test_move_to_stacker_writes_expected_frame(self):
    backend, writer = _make_backend([Frame(0x69, b"\x65").to_bytes()])
    await backend.move_to_stacker(3)
    self.assertEqual(writer.sent.hex(), "650a0001020000204100000000")

  async def test_save_teachpoint_writes_captured_shape(self):
    backend, writer = _make_backend([])
    await backend.save_teachpoint(TEST_LEFT_TEACHPOINT)
    self.assertEqual(
      writer.sent.hex(),
      "731b001f5bffb342ad70b4c3000020c100010000a041000000000000c0bf",
    )

  async def test_device_error_raises(self):
    payload = b"X position out of bounds"
    frame = Frame(0x02, payload).to_bytes()
    backend, writer = _make_backend([frame])
    with self.assertRaises(BenchCelDeviceError) as cm:
      await backend.move_x(500)
    self.assertEqual(writer.sent.hex(), Frame(0x66, struct.pack("<Bf", 1, 500.0)).hex())
    self.assertEqual(cm.exception.message, "X position out of bounds")

  async def test_request_stacker_sensors(self):
    response = Frame(0x7E, _sensor_payload(stacker_index=2)).to_bytes()
    backend, writer = _make_backend([response])
    status = await backend.request_stacker_sensors(3)
    self.assertEqual(writer.sent.hex(), "7e010002")
    self.assertEqual(status.stacker, 3)
    self.assertEqual(status.plate_presence, 128)

  async def test_request_axis_bounds(self):
    payload = struct.pack("<8f", -115.0, -360.9, -1.5, -1.5, 115.0, 360.9, 104.0, 11.0)
    backend, writer = _make_backend([Frame(0x99, payload).to_bytes()])
    bounds = await backend.request_axis_bounds()
    self.assertEqual(writer.sent.hex(), "990000")
    self.assertAlmostEqual(bounds.theta_min, -115.0)
    self.assertAlmostEqual(bounds.x_max, 360.9, places=3)
    self.assertAlmostEqual(bounds.gripper_max, 11.0)

  async def test_open_and_close_stacker_grippers(self):
    backend, writer = _make_backend(
      [
        Frame(0x69, b"\x67").to_bytes(),
        Frame(0x69, b"\x67").to_bytes(),
      ]
    )
    await backend.dangerously_open_stacker_grippers(1)
    await backend.close_stacker_grippers(1)
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
    await backend.downstack_plate(3)
    await backend.upstack_plate(3)
    await backend.load_stacker(3)
    await backend.unload_stacker(3)
    self.assertEqual(
      writer.sent.hex(),
      "62040001020001"  # downstack stacker 3
      "63040001020001"  # upstack stacker 3
      "6002000102"  # load stacker 3
      "610600010200000000",  # unload stacker 3
    )

  async def test_serialize_includes_connection_info(self):
    plate = Plate("plate", size_x=127.76, size_y=85.48, size_z=14.6, ordered_items={})
    backend = BenchCel4RBackend(
      host="192.168.0.100",
      port=7612,
      timeout=12.5,
      labware=plate,
    )
    serialized = backend.serialize()
    self.assertEqual(serialized["type"], "BenchCel4RBackend")
    self.assertEqual(serialized["host"], "192.168.0.100")
    self.assertEqual(serialized["timeout"], 12.5)
    self.assertEqual(serialized["labware"]["name"], "plate")
    self.assertAlmostEqual(serialized["labware"]["stacking_thickness"], 13.1)
    deserialized = StackerBackend.deserialize(serialized.copy())
    self.assertIsInstance(deserialized, BenchCel4RBackend)
    self.assertEqual(deserialized.host, "192.168.0.100")
    self.assertEqual(deserialized.labware_settings.name, "plate")


class BenchCelStackerMappingTests(unittest.IsolatedAsyncioTestCase):
  async def test_transfers_require_a_configured_teachpoint(self):
    backend = BenchCel4RBackend(host="ignored")  # no loading_tray_teachpoint_id
    self.assertIsNone(backend.loading_tray_teachpoint_id)
    stacks = benchcel_4r_stacks()
    await backend.set_stacks(stacks)
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})

    with self.assertRaisesRegex(ValueError, "teachpoint"):
      await backend.downstack(stacks[0])
    with self.assertRaisesRegex(ValueError, "teachpoint"):
      await backend.upstack(stacks[0], plate)

  async def test_downstack_maps_stack_to_stacker(self):
    backend = BenchCel4RBackend(host="ignored")
    stacks = benchcel_4r_stacks()
    await backend.set_stacks(stacks)

    sent: list[Frame] = []

    async def fake_send(frame: Frame, **kwargs) -> Frame:
      sent.append(frame)
      return Frame(0x69, kwargs.get("ack_payload") or bytes([frame.command_id]))

    backend._send_frame_expect_ack_no_lock = fake_send  # type: ignore[method-assign]
    # stacks[2] is human stacker 3 (zero-based index 0x02)
    await backend.downstack(stacks[2], teachpoint_id=0x1E)

    self.assertEqual(
      [f.hex() for f in sent],
      ["6a010001", "62040001020001", "630400011e0001"],
    )

  async def test_upstack_maps_stack_to_stacker(self):
    backend = BenchCel4RBackend(host="ignored")
    stacks = benchcel_4r_stacks()
    await backend.set_stacks(stacks)
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})

    sent: list[Frame] = []

    async def fake_send(frame: Frame, **kwargs) -> Frame:
      sent.append(frame)
      return Frame(0x69, kwargs.get("ack_payload") or bytes([frame.command_id]))

    backend._send_frame_expect_ack_no_lock = fake_send  # type: ignore[method-assign]
    # stacks[0] is human stacker 1 (zero-based index 0x00)
    await backend.upstack(stacks[0], plate, teachpoint_id=0x1E)

    self.assertEqual(
      [f.hex() for f in sent],
      ["6a010001", "620400011e0001", "63040001000001"],
    )


if __name__ == "__main__":
  unittest.main()
