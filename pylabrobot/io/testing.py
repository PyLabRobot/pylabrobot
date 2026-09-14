"""Transport doubles for driver tests using native unittest.mock assertions."""

from contextlib import contextmanager
from typing import Callable, Iterator, Optional, cast
from unittest.mock import Mock, NonCallableMagicMock, create_autospec

from pylabrobot.io.serial import Serial


def fake_serial(
  *,
  incoming: bytes = b"",
  on_write: Optional[Callable[[bytes], Optional[bytes]]] = None,
  port: str = "FAKE",
  read_timeout: float = 1.0,
) -> NonCallableMagicMock:
  """Create a buffered Serial mock without constructing or opening a transport.

  Reads consume at most the requested number of bytes and return immediately, even when the
  buffer is empty. Non-positive read sizes return empty bytes without consuming input, matching
  pyserial. Setup and stop are async no-ops; timeouts are fixture-local values, without timing
  or connection-state simulation. Protocol encoding and response framing belong in the test.

  Args:
    incoming: Bytes already buffered when the fixture is created.
    on_write: Synchronous callback invoked when a write is awaited. Its returned bytes are
      appended to the receive buffer; None appends nothing. Exceptions propagate to the caller.
    port: Value exposed by the mock's port property.
    read_timeout: Initial value returned by get_read_timeout().

  Returns:
    An autospecced Serial instance mock with native call and await assertions. Public methods
    outside read, write, setup, stop, reset_input_buffer and the three timeout methods raise
    NotImplementedError. To configure one, replace its side_effect, or clear side_effect before
    setting return_value (for example, io.readline.side_effect = None).
  """
  io = cast(NonCallableMagicMock, create_autospec(Serial, instance=True, spec_set=True))
  rx = bytearray(incoming)

  # Serial also exposes file methods inherited from the standard library's IOBase.
  unsupported_methods: dict[str, Mock] = {
    "readline": io.readline,
    "send_break": io.send_break,
    "reset_output_buffer": io.reset_output_buffer,
    "serialize": io.serialize,
    "close": io.close,
    "fileno": io.fileno,
    "flush": io.flush,
    "isatty": io.isatty,
    "readable": io.readable,
    "readlines": io.readlines,
    "seek": io.seek,
    "seekable": io.seekable,
    "tell": io.tell,
    "truncate": io.truncate,
    "writable": io.writable,
    "writelines": io.writelines,
  }
  for name, method in unsupported_methods.items():
    method.side_effect = NotImplementedError(
      f"fake_serial does not support Serial.{name}(); configure its side_effect explicitly."
    )

  def read(num_bytes: int = 1) -> bytes:
    """Consume at most num_bytes from the front of the receive buffer."""
    if num_bytes <= 0:
      return b""
    data = bytes(rx[:num_bytes])
    del rx[:num_bytes]
    return data

  def write(data: bytes) -> None:
    """Append a test-local response after the AsyncMock records the awaited write."""
    if on_write is not None:
      response = on_write(data)
      if response is not None:
        rx.extend(response)

  def get_read_timeout() -> float:
    """Return the fixture-local read timeout."""
    return read_timeout

  def set_read_timeout(timeout: float) -> None:
    """Update the fixture-local read timeout without simulating elapsed time."""
    nonlocal read_timeout
    read_timeout = timeout

  @contextmanager
  def temporary_timeout(timeout: float) -> Iterator[None]:
    """Restore the previous timeout on exit, including when the block raises."""
    original = get_read_timeout()
    set_read_timeout(timeout)
    try:
      yield
    finally:
      set_read_timeout(original)

  io.port = port
  io.setup.return_value = None
  io.stop.return_value = None
  io.read.side_effect = read
  io.write.side_effect = write
  io.reset_input_buffer.side_effect = rx.clear
  io.get_read_timeout.side_effect = get_read_timeout
  io.set_read_timeout.side_effect = set_read_timeout
  io.temporary_timeout.side_effect = temporary_timeout
  return io
