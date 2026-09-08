"""Prep integration with real TCP sessions over framed, in-memory I/O."""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.prep import PrepChatterboxClient
from pylabrobot.hamilton.prep import prep_commands as C
from pylabrobot.hamilton.prep.client import MLPREP_OBJECT_PATH, PIPETTOR_OBJECT_PATH, PrepClient
from pylabrobot.hamilton.prep.error_tables import PREP_ERROR_CODES
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.introspection import ObjectInfo
from pylabrobot.hamilton.transport.tcp.messages import HoiParams
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, HoiPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.tests.tcp_tests import _MemorySocket, _response, _SessionTest
from pylabrobot.hamilton.transport.tcp.wire_types import F32, Str
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError


class TestPrepTransport(_SessionTest):
  """Check request binding, typed responses, and failure ownership end to end."""

  def start_session(self, client: PrepClient, address: Address) -> _MemorySocket:
    """Install a fresh production session without opening a network connection."""
    io = _MemorySocket()
    client._session = TCPSession(io, error_codes=PREP_ERROR_CODES)
    client._session.client_address = Address(2, 1, 65535)
    client._session.state = SessionState.CONNECTING
    for path in (MLPREP_OBJECT_PATH, PIPETTOR_OBJECT_PATH):
      client.registry.register(path, ObjectInfo(path.rsplit(".", 1)[-1], "", 0, 0, address))
    client._session.start_reader()
    return io

  def make_client(self) -> tuple[PrepClient, _MemorySocket]:
    """Provide a Prep client using the production reader and transaction code."""
    client = PrepClient("memory-only", 0)
    io = self.start_session(client, Address(1, 1, 257))
    self.addAsyncCleanup(client.stop)
    return client, io

  async def test_reusable_request_rebinds_after_reconnection(self):
    client, io = self.make_client()
    command = C.PrepGetDefaultTraverseHeight()
    for address in (Address(1, 1, 257), Address(1, 2, 300)):
      if address.node == 2:
        await client.stop()
        io = self.start_session(client, address)

      async def respond(request: HarpPacket) -> None:
        """Return the requested scalar to the current session."""
        self.assertEqual(request.dst, address)
        io.feed(
          _response(
            source=address,
            sequence=request.seq,
            action=Hoi2Action.STATUS_RESPONSE,
            params=HoiParams().add(180.0, F32).build(),
          )
        )

      io.on_write = respond
      responses = await asyncio.gather(client.execute(command), client.execute(command))
      self.assertEqual(responses, [C.PrepGetDefaultTraverseHeight.Response(180.0)] * 2)
      self.assertEqual([request.seq for request in io.writes], [1, 2])
      self.assertEqual(command, C.PrepGetDefaultTraverseHeight())
    with self.assertRaises(FrozenInstanceError):
      command.dest = Address(1, 1, 999)  # type: ignore[misc]

  async def test_identity_query_uses_runtime_interface_and_method(self):
    client, io = self.make_client()

    async def respond(request: HarpPacket) -> None:
      """Inspect the real encoded probe and return a string fragment."""
      hoi = HoiPacket.unpack(request.payload)
      self.assertEqual(
        (hoi.interface_id, hoi.action_id, hoi.action_code), (3, 9, Hoi2Action.STATUS_REQUEST)
      )
      io.feed(
        _response(
          sequence=request.seq,
          action=Hoi2Action.STATUS_RESPONSE,
          params=HoiParams().add("PREP123", Str).build(),
        )
      )

    io.on_write = respond
    self.assertEqual(await client._query_firmware_string(Address(1, 1, 257), 9), "PREP123")

  async def test_execute_raises_firmware_error_and_exchange_preserves_frame(self):
    client, io = self.make_client()
    payload = HoiParams().add("0x0001.0x0001.0x0101:0x01,0x0006,0x0F08", Str).build()

    async def respond(request: HarpPacket) -> None:
      """Return an error without accepting any diagnostic round trips."""
      io.feed(_response(sequence=request.seq, action=Hoi2Action.COMMAND_EXCEPTION, params=payload))

    io.on_write = respond
    with self.assertRaises(HoiError) as caught:
      await client.execute(C.PrepPark())
    self.assertEqual(caught.exception.raw_response, payload)
    self.assertEqual(len(io.writes), 1)
    raw = await client.exchange(C.PrepPark())
    self.assertEqual(raw.hoi.params, payload)
    self.assertEqual(raw.hoi.action_code, Hoi2Action.COMMAND_EXCEPTION)
    self.assertEqual(client.connection_info.state, SessionState.READY)

  async def test_errors_follow_selected_channels_instead_of_firmware_ordinals(self):
    client, io = self.make_client()
    command = C.PrepDispenseInitToWaste(
      waste_parameters=[
        C.DispenseInitToWasteParameters(False, C.ChannelIndex.FrontChannel, 12.5, 12.5, 12.5),
      ]
    )
    payload = HoiParams().add("0x0001.0x0001.0x0101:0x01,0x0006,0x0F08", Str).build()

    async def respond(request: HarpPacket) -> None:
      """Attribute the first firmware result to the selected front channel."""
      io.feed(_response(sequence=request.seq, action=Hoi2Action.COMMAND_EXCEPTION, params=payload))

    io.on_write = respond
    with self.assertRaises(ChannelizedError) as caught:
      await client.execute(command)
    self.assertEqual(set(caught.exception.errors), {1})

  async def test_cancellation_prevents_further_prep_queries(self):
    client, io = self.make_client()
    task = asyncio.create_task(client.execute(C.PrepGetDefaultTraverseHeight()))
    await self.wait_until(lambda: len(io.writes) == 1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(client.connection_info.state, SessionState.UNCERTAIN)
    with self.assertRaises(ConnectionError):
      await client.execute(C.PrepPark())
    self.assertEqual(len(io.writes), 1)

  async def test_failed_prep_identity_check_closes_the_session(self):
    client, io = self.make_client()
    with (
      patch.object(HamiltonTCPClient, "setup", new=AsyncMock()),
      patch.object(client, "discovered_root_name", new=AsyncMock(return_value="WrongRoot")),
    ):
      with self.assertRaisesRegex(RuntimeError, "Wrong instrument"):
        await client.setup()
    self.assertTrue(io.closed)
    self.assertEqual(client.connection_info.state, SessionState.CLOSED)

  async def test_chatterbox_decodes_queries_and_invalidates_retained_discovery(self):
    client = PrepChatterboxClient()
    await client.setup()
    self.addAsyncCleanup(client.stop)
    intro = client.introspection
    address = await client.resolve_path(PIPETTOR_OBJECT_PATH)
    result = await client.execute(C.PrepGetIsInitialized(dest=client.mlprep_address))
    self.assertIsInstance(result, C.PrepGetIsInitialized.Response)
    frame = await client.exchange(C.PrepPark())
    self.assertEqual(frame.harp.action_code, 4)
    await client.stop()
    await client.setup()
    with self.assertRaises(ConnectionError):
      await intro.methods_for_interface(address, 1)

  def test_nested_move_payload_matches_firmware_bytes(self):
    command = C.PrepMoveToPosition(
      move_parameters=C.GantryMoveXYZParameters(
        default_values=True,
        gantry_x_position=12.5,
        axis_parameters=[C.ChannelYZMoveParameters(True, C.ChannelIndex.RearChannel, 12.5, 12.5)],
      )
    )
    self.assertEqual(
      command.build_parameters().build(),
      bytes.fromhex(
        "1e00340017010200010028000400000048411f0022001e001e00"
        "170102000100200004000200000028000400000048412800040000004841"
      ),
    )

  async def test_channel_bounds_decode_nested_structures_in_channel_order(self):
    from pylabrobot.hamilton.prep.channels import request_channel_bounds

    client, io = self.make_client()
    client.registry.register(
      C.PrepGetChannelBounds.firmware_path,
      ObjectInfo("PipettorService", "", 0, 0, Address(1, 1, 257)),
    )
    # GetChannelBounds response from MLPrep Runtime V3.0.20.675 (PRPBD1394).
    payload = bytes.fromhex(
      "1f0078001e003800200004000100000028000400c095033f28000400cbc19543"
      "28000400000010c1280004000000bc432800040000009c412800040000802743"
      "1e003800200004000200000028000400c095033f28000400cbc1954328000400"
      "00000000280004000080c0432800040000009c412800040000802743"
    )

    async def respond(request: HarpPacket) -> None:
      """Return a real structure array in firmware channel order."""
      io.feed(
        _response(
          sequence=request.seq,
          action=Hoi2Action.STATUS_RESPONSE,
          params=payload,
        )
      )

    io.on_write = respond
    bounds = await request_channel_bounds(client)
    self.assertEqual(
      bounds,
      [
        dict(
          x_min=0.5140037536621094,
          x_max=299.5140075683594,
          y_min=0,
          y_max=385,
          z_min=19.5,
          z_max=167.5,
        ),
        dict(
          x_min=0.5140037536621094,
          x_max=299.5140075683594,
          y_min=-9,
          y_max=376,
          z_min=19.5,
          z_max=167.5,
        ),
      ],
    )

  async def test_reconnection_during_path_resolution_cannot_send_an_old_address(self):
    for raw in (False, True):
      client, io = self.make_client()
      fresh_sockets: list[_MemorySocket] = []

      async def resolve(path: str) -> Address:
        """Replace the session while a firmware-path lookup is in flight."""
        await client.stop()
        fresh = self.start_session(client, Address(1, 2, 300))
        fresh.on_write = AsyncMock(side_effect=AssertionError("stale address reached new session"))
        fresh_sockets.append(fresh)
        return Address(1, 1, 257)

      client.resolve_path = resolve  # type: ignore[method-assign]
      with self.assertRaises(ConnectionError):
        if raw:
          await client.exchange(C.PrepPark())
        else:
          await client.execute(C.PrepPark())
      self.assertTrue(io.closed)
      self.assertEqual(fresh_sockets[0].writes, [])
      self.assertEqual(client.connection_info.state, SessionState.READY)
