"""Contract tests for PyLabRobot I/O transports."""

import warnings
from typing import Type

import pytest

import pylabrobot.io.usb as usb_module
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI, FTDIValidator
from pylabrobot.io.hid import HID, HIDValidator
from pylabrobot.io.io import ContinuousConnectionIO, IOBase, ReconnectableConnectionIO
from pylabrobot.io.serial import Serial, SerialValidator
from pylabrobot.io.socket import Socket, SocketValidator
from pylabrobot.io.usb import USB, USBValidator
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

TRANSPORT_TYPES = (Serial, FTDI, USB, HID, Socket)
VALIDATOR_TYPES = (SerialValidator, FTDIValidator, USBValidator, HIDValidator, SocketValidator)


def test_default_transport_read_deadlines_do_not_warn() -> None:
  """Default construction does not opt into a deprecated public read timeout."""
  with warnings.catch_warnings(record=True) as captured:
    warnings.simplefilter("always", DeprecationWarning)
    socket = Socket("socket", "127.0.0.1", 1)
    usb = USB(1, 2, "USB", packet_read_timeout=0.01)
    serial = Serial("serial", port="fake")
    HID("HID", 1, 2)
  assert socket._read_timeout is None
  assert usb.read_timeout is None
  assert serial.timeout is None
  assert not [warning for warning in captured if issubclass(warning.category, DeprecationWarning)]


def test_explicit_public_constructor_deadlines_warn_even_when_none() -> None:
  """An explicitly supplied compatibility setting is never silently ignored."""
  with pytest.warns(DeprecationWarning, match="Socket.read_timeout"):
    Socket("socket", "127.0.0.1", 1, read_timeout=None)
  with pytest.warns(DeprecationWarning, match="USB.read_timeout"):
    usb = USB(1, 2, "USB", packet_read_timeout=0.01, read_timeout=1)
  with pytest.warns(DeprecationWarning, match="Serial.timeout"):
    Serial("serial", port="fake", timeout=None)
  assert usb.read_timeout == 1


@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES)
def test_transport_inherits_plr_io_base(transport_type: Type[IOBase]) -> None:
  """Require the shared PLR base to provide all common transport behavior."""
  assert issubclass(transport_type, IOBase)


@pytest.mark.parametrize("validator_type", VALIDATOR_TYPES)
def test_validator_is_an_io_base_subclass(validator_type: Type[IOBase]) -> None:
  """Keep validation transports substitutable for ordinary I/O transports."""
  assert issubclass(validator_type, IOBase)


def test_io_base_has_a_lifespan() -> None:
  """Give every PLR I/O implementation the scoped lifespan contract."""
  assert issubclass(IOBase, HasLifespan)


class TestIOBaseComposition(AnyioTestBase):
  """Keep the abstract transport hook cooperative while requiring a real override."""

  async def test_abstract_io_base_supports_super_entry(self) -> None:
    """An I/O subtype can enter its ABC layer and still own a scoped handle."""
    with pytest.raises(TypeError, match="abstract"):
      IOBase()  # type: ignore[abstract]

    events: list[str] = []

    class ComposedIO(IOBase):
      """Minimal transport with a cooperative lifespan entry."""

      async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
        """Compose abstract entry before registering owned cleanup."""
        await super()._enter_lifespan(stack)
        events.append("acquired")
        stack.callback(events.append, "released")

      async def write(self, data: bytes) -> None:
        """Consume a test message without hardware."""

      async def read(self) -> bytes:
        """Return a test message without hardware."""
        return b""

    async with ComposedIO() as transport:
      assert transport.is_active
      events.append("body")

    assert events == ["acquired", "body", "released"]


def test_connection_continuity_is_an_opt_in_contract() -> None:
  """A stateless I/O double does not promise stable connection identity."""
  assert issubclass(ContinuousConnectionIO, IOBase)
  assert not issubclass(MockIO, ContinuousConnectionIO)
  assert isinstance(MockIO(), IOBase)


def test_reconnectable_io_refines_connection_continuity() -> None:
  """An explicit reconnect operation is the only extra way to change identity."""
  assert issubclass(ReconnectableConnectionIO, ContinuousConnectionIO)
  for reconnectable_type in (Socket, FTDI, USB):
    assert issubclass(reconnectable_type, ReconnectableConnectionIO)
    assert issubclass(reconnectable_type, ContinuousConnectionIO)
  for continuous_type in (Serial, HID):
    assert issubclass(continuous_type, ContinuousConnectionIO)
    assert not issubclass(continuous_type, ReconnectableConnectionIO)


@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES)
def test_transport_implements_its_own_lifespan(transport_type: Type[IOBase]) -> None:
  """Prevent transport-specific acquisition from falling back to the abstract contract."""
  assert transport_type._enter_lifespan is not IOBase._enter_lifespan


@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES)
def test_transport_inherits_managed_lifecycle_methods(transport_type: Type[IOBase]) -> None:
  """Keep lifecycle state and compatibility bridges owned by the shared base."""
  assert transport_type.setup is IOBase.setup
  assert transport_type.stop is IOBase.stop
  assert transport_type.__aenter__ is IOBase.__aenter__
  assert transport_type.__aexit__ is IOBase.__aexit__


def test_mock_io_implements_an_explicit_lifespan() -> None:
  """Keep the intentionally stateless mock's lifecycle decision explicit."""
  assert MockIO._enter_lifespan is not IOBase._enter_lifespan


class TestMockIOLifespan(AnyioTestBase):
  """Exercise MockIO structured entry on asyncio and Trio."""

  async def test_mock_io_is_active_only_inside_its_lifespan(self) -> None:
    """Enter and leave the stateless mock through the shared resource machinery."""
    io = MockIO()

    assert not io.is_active
    async with io:
      assert io.is_active
    assert not io.is_active


class _RecordingUSB(USB):
  """Record USB setup options without accessing hardware."""

  def _setup_sync(self, empty_buffer: bool) -> None:
    """Retain the bridge option received by USB's real lifespan."""
    self.received_empty_buffer = empty_buffer


class TestUSBSetupBridge(AnyioTestBase):
  """Exercise the asyncio-only interactive compatibility bridge."""

  _anyio_backends = ["asyncio"]

  async def test_setup_forwards_empty_buffer_to_usb_lifespan(self) -> None:
    """Preserve non-default USB options while setup delegates to lifespan."""
    io = _RecordingUSB(id_vendor=1, id_product=2, human_readable_device_name="test")
    use_usb = usb_module.USE_USB
    usb_module.USE_USB = True

    try:
      with pytest.warns(DeprecationWarning):
        await io.setup(empty_buffer=False)
      try:
        assert io.received_empty_buffer is False
      finally:
        await io.stop()
    finally:
      usb_module.USE_USB = use_usb
