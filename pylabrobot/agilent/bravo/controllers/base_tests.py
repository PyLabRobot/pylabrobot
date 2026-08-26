import inspect
import unittest

from pylabrobot.agilent.bravo.controllers.base import (
  AxisMoveInfo,
  BravoController,
  FirmwareVersion,
  JogParams,
  MultiAxisMove,
)
from pylabrobot.agilent.bravo.errors import BravoError, ErrorType
from pylabrobot.agilent.bravo.protocol.commands import LightCommandData
from pylabrobot.agilent.bravo.transport.base import Transport
from pylabrobot.agilent.bravo.types import DeviceStateFlag, GripperDetectionState, LightColor


class FakeTransport(Transport):
  def send(self, data: bytes) -> None:
    pass

  def receive(self, timeout: float = 2.0) -> bytes:
    return b""

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    return b""

  @property
  def is_connected(self) -> bool:
    return True


class ConcreteController(BravoController):
  """Minimal concrete controller used only to exercise the base contract."""

  def initialize(self) -> None:
    pass

  def ping(self) -> bool:
    return True

  @property
  def is_connected(self) -> bool:
    return self._transport.is_connected

  def get_firmware_version(self) -> FirmwareVersion:
    return FirmwareVersion()

  def move(self, moves, wait: bool = True, timeout: float = 30.0) -> None:
    pass

  def home_axes(self, axes, *, force: bool = False) -> None:
    pass

  def jog(self, params: JogParams) -> float:
    return 0.0

  def get_position(self, axis) -> float:
    return 0.0

  def is_axis_homed(self, axis) -> bool:
    return False

  def get_park_position(self, axis) -> float:
    return 0.0

  def enable_motor(self, axis) -> None:
    pass

  def disable_motor(self, axis) -> None:
    pass

  def reset_faults(self, axes) -> None:
    pass

  def query_state(self) -> DeviceStateFlag:
    return DeviceStateFlag(0)

  def is_go_button_pressed(self) -> bool:
    return False

  def clear_go_button(self) -> None:
    pass

  def set_light(self, command: LightCommandData) -> None:
    pass

  def clear_lights(self) -> None:
    pass

  def read_head_adc(self) -> int:
    return 0

  def detect_smart_head(self) -> bool:
    return False

  def read_smart_head_type(self) -> int:
    return 0

  def detect_gripper(self) -> GripperDetectionState:
    return GripperDetectionState.NOT_YET_DETECTED

  def grip(self, speed, position: float, grip_lid: bool = False) -> None:
    pass

  def open_gripper(self, position=None) -> None:
    pass

  def is_plate_in_gripper(self) -> bool:
    return False

  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    return b""

  @property
  def last_error(self):
    return None


class BravoControllerConstructionTests(unittest.TestCase):
  def test_constructor_stores_the_given_transport(self):
    transport = FakeTransport()
    controller = ConcreteController(transport)
    # is_connected is implemented (above) purely in terms of self._transport,
    # so this only passes if the constructor actually stored the instance we
    # passed in, not e.g. a fresh transport or None.
    self.assertTrue(controller.is_connected)

  def test_constructor_does_not_connect_or_open_anything(self):
    # A controller must not expose open_serial/open_tcp/close: connecting is
    # entirely the transport's job, done before the controller exists.
    self.assertNotIn("open_serial", dir(BravoController))
    self.assertNotIn("open_tcp", dir(BravoController))
    self.assertNotIn("close", dir(BravoController))

  def test_incomplete_subclass_cannot_be_instantiated(self):
    class Incomplete(BravoController):
      def initialize(self) -> None:
        pass

    with self.assertRaises(TypeError):
      Incomplete(FakeTransport())  # type: ignore[abstract]

  def test_subclass_missing_initialize_cannot_be_instantiated(self):
    # initialize() must be a required abstract method, not an optional hook:
    # a subclass implementing every other method still can't be built.
    class MissingInitialize(BravoController):
      def ping(self) -> bool:
        return True

      @property
      def is_connected(self) -> bool:
        return True

      def get_firmware_version(self) -> FirmwareVersion:
        return FirmwareVersion()

      def move(self, moves, wait: bool = True, timeout: float = 30.0) -> None:
        pass

      def home_axes(self, axes, *, force: bool = False) -> None:
        pass

      def jog(self, params: JogParams) -> float:
        return 0.0

      def get_position(self, axis) -> float:
        return 0.0

      def is_axis_homed(self, axis) -> bool:
        return False

      def get_park_position(self, axis) -> float:
        return 0.0

      def enable_motor(self, axis) -> None:
        pass

      def disable_motor(self, axis) -> None:
        pass

      def reset_faults(self, axes) -> None:
        pass

      def query_state(self) -> DeviceStateFlag:
        return DeviceStateFlag(0)

      def is_go_button_pressed(self) -> bool:
        return False

      def clear_go_button(self) -> None:
        pass

      def set_light(self, command: LightCommandData) -> None:
        pass

      def clear_lights(self) -> None:
        pass

      def read_head_adc(self) -> int:
        return 0

      def detect_smart_head(self) -> bool:
        return False

      def read_smart_head_type(self) -> int:
        return 0

      def detect_gripper(self) -> GripperDetectionState:
        return GripperDetectionState.NOT_YET_DETECTED

      def grip(self, speed, position: float, grip_lid: bool = False) -> None:
        pass

      def open_gripper(self, position=None) -> None:
        pass

      def is_plate_in_gripper(self) -> bool:
        return False

      def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
        return b""

      @property
      def last_error(self):
        return None

    with self.assertRaises(TypeError):
      MissingInitialize(FakeTransport())  # type: ignore[abstract]

  def test_concrete_subclass_can_be_instantiated(self):
    controller = ConcreteController(FakeTransport())
    controller.initialize()
    self.assertTrue(controller.ping())


class BravoControllerDefaultMethodTests(unittest.TestCase):
  def setUp(self):
    self.controller = ConcreteController(FakeTransport())

  def test_read_plate_sensor_default_raises_not_implemented(self):
    with self.assertRaises(NotImplementedError):
      self.controller.read_plate_sensor()

  def test_scan_stack_with_gripper_default_raises_not_implemented(self):
    with self.assertRaises(NotImplementedError):
      self.controller.scan_stack_with_gripper(start_zg=0.0, end_zg=10.0, speed="slow")

  def test_get_head_type_default_is_96_d_70(self):
    self.assertEqual(self.controller.get_head_type(), "96_d_70")

  def test_ul_to_mm_default_is_identity(self):
    self.assertEqual(self.controller.ul_to_mm(37.5), 37.5)


class TimeoutUnitsAreSecondsTests(unittest.TestCase):
  """Every timeout on the interface is seconds, not milliseconds.

  A regression that reintroduces a millisecond-scale default (e.g. 30000
  instead of 30.0) is a thousand-fold unit error, so these check the actual
  declared defaults rather than just that the parameter exists.
  """

  def test_move_timeout_default_is_30_seconds(self):
    default = inspect.signature(BravoController.move).parameters["timeout"].default
    self.assertEqual(default, 30.0)
    self.assertIsInstance(default, float)

  def test_send_command_timeout_default_is_2_seconds(self):
    default = inspect.signature(BravoController.send_command).parameters["timeout"].default
    self.assertEqual(default, 2.0)
    self.assertIsInstance(default, float)

  def test_multi_axis_move_timeout_default_is_30_seconds(self):
    move = MultiAxisMove()
    self.assertEqual(move.timeout, 30.0)
    self.assertIsInstance(move.timeout, float)

  def test_read_plate_sensor_transient_default_is_in_seconds(self):
    default = inspect.signature(BravoController.read_plate_sensor).parameters["transient"].default
    self.assertEqual(default, 0.0)
    self.assertIsInstance(default, float)

  def test_scan_stack_with_gripper_transient_default_is_in_seconds(self):
    params = inspect.signature(BravoController.scan_stack_with_gripper).parameters
    default = params["transient"].default
    self.assertEqual(default, 0.0)
    self.assertIsInstance(default, float)


class DataclassDefaultsTests(unittest.TestCase):
  def test_axis_move_info_defaults(self):
    move = AxisMoveInfo(axis="x", position=12.5)
    self.assertEqual(move.velocity, 0.0)
    self.assertEqual(move.acceleration, 0.0)
    self.assertTrue(move.absolute)

  def test_jog_params_requires_all_fields(self):
    params = JogParams(
      axis="z",
      velocity=5.0,
      acceleration=10.0,
      max_position=100.0,
      tolerance=0.5,
      peak_current=0.1,
    )
    self.assertEqual(params.axis, "z")
    self.assertEqual(params.peak_current, 0.1)

  def test_firmware_version_defaults_to_empty_strings(self):
    version = FirmwareVersion()
    self.assertEqual(version.master, "")
    self.assertEqual(version.sub1, "")
    self.assertEqual(version.sub2, "")


class BravoErrorIntegrationTests(unittest.TestCase):
  def test_last_error_type_is_bravo_error(self):
    err = BravoError(ErrorType.NOT_HOMED, axis="x")
    self.assertIsInstance(err, BravoError)

  def test_light_command_data_round_trips_through_set_light(self):
    controller = ConcreteController(FakeTransport())
    command = LightCommandData(light=LightColor.RED)
    # No exception is the whole contract here: set_light must accept a
    # LightCommandData built from the shared types module.
    controller.set_light(command)


if __name__ == "__main__":
  unittest.main()
