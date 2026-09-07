import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.io.socket import Socket


class SocketSourceIPTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_binds_to_source_ip(self):
    socket = Socket(
      human_readable_device_name="test",
      host="192.0.2.10",
      port=7612,
      source_ip="192.0.2.20",
    )

    with patch(
      "pylabrobot.io.socket.asyncio.open_connection",
      new_callable=AsyncMock,
    ) as open_connection:
      open_connection.return_value = (object(), object())
      await socket.setup()

    open_connection.assert_awaited_once_with(
      host="192.0.2.10",
      port=7612,
      ssl=None,
      server_hostname=None,
      local_addr=("192.0.2.20", 0),
    )

  async def test_setup_without_source_ip_uses_default_binding(self):
    socket = Socket(
      human_readable_device_name="test",
      host="192.0.2.10",
      port=7612,
    )

    with patch(
      "pylabrobot.io.socket.asyncio.open_connection",
      new_callable=AsyncMock,
    ) as open_connection:
      open_connection.return_value = (object(), object())
      await socket.setup()

    open_connection.assert_awaited_once_with(
      host="192.0.2.10",
      port=7612,
      ssl=None,
      server_hostname=None,
      local_addr=None,
    )


if __name__ == "__main__":
  unittest.main()
