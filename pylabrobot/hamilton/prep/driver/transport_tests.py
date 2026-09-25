"""Prep integration with real TCP sessions over framed, in-memory I/O."""

import asyncio
import logging
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as C
from pylabrobot.hamilton.prep.driver.errors import PREP_ERROR_CODES
from pylabrobot.hamilton.prep.driver.features.pipettes import PipetteChannel, Pipettes
from pylabrobot.hamilton.prep.driver.master import _PrepTCPSession
from pylabrobot.hamilton.prep.driver.prep_commands import MLPREP_OBJECT_PATH, PIPETTOR_OBJECT_PATH
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.introspection import MethodInfo, ObjectInfo
from pylabrobot.hamilton.transport.tcp.messages import HoiParams
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, HoiPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.tests.tcp_tests import _MemorySocket, _response, _SessionTest
from pylabrobot.hamilton.transport.tcp.wire_types import F32, Str
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.resources.hamilton import PrepDeck


class TestPrepTransport(_SessionTest):
  """Check request binding, typed responses, and failure ownership end to end."""

  def start_session(
    self, driver: PrepDriver, address: Address, session_type: type = TCPSession
  ) -> _MemorySocket:
    """Install a fresh production session without opening a network connection."""
    io = _MemorySocket()
    driver.io._session = session_type(io, error_codes=PREP_ERROR_CODES)
    driver.io._session.client_address = Address(2, 1, 65535)
    driver.io._session.state = SessionState.CONNECTING
    for path in (MLPREP_OBJECT_PATH, PIPETTOR_OBJECT_PATH):
      driver.io.registry.register(path, ObjectInfo(path.rsplit(".", 1)[-1], "", 0, 0, address))
    driver.io._session.start_reader()
    return io

  def make_driver(self) -> tuple[PrepDriver, _MemorySocket]:
    """Provide a Prep driver using the production reader and transaction code."""
    driver = PrepDriver(deck=PrepDeck(), host="memory-only", port=0)
    io = self.start_session(driver, Address(1, 1, 257))
    self.addAsyncCleanup(driver.io.stop)
    return driver, io

  async def test_tip_presence_uses_each_objects_named_method_and_cached_table(self):
    """Sensor IDs come from discovery; repeated reads reuse the session's method tables."""
    client, io = self.make_driver()
    channels = Pipettes(client)
    rear, front = Address(1, 236, 514), Address(1, 237, 514)
    tables = {
      rear: [
        MethodInfo(3, 0, 15, "UnrelatedMethod"),
        MethodInfo(4, 0, 88, "GetTipPresent"),
      ],
      front: [
        MethodInfo(2, 0, 21, "GetTipPresent"),
        MethodInfo(3, 0, 15, "UnrelatedMethod"),
      ],
    }

    async def respond(request: HarpPacket) -> None:
      """Only answer the discovered sensor query on its own object."""
      hoi = HoiPacket.unpack(request.payload)
      ids = (4, 88) if request.dst == rear else (2, 21)
      self.assertEqual((hoi.interface_id, hoi.action_id), ids)
      self.assertEqual(hoi.action_code, Hoi2Action.STATUS_REQUEST)
      self.assertEqual(hoi.params, b"")
      io.feed(
        _response(
          source=request.dst,
          sequence=request.seq,
          action=Hoi2Action.STATUS_RESPONSE,
          params=bytes.fromhex("0600040001000000" if request.dst == rear else "0600040000000000"),
        )
      )

    io.on_write = respond
    channels.channels = [
      PipetteChannel(index=0, driver=client, sleeve_sensor=rear),
      PipetteChannel(index=1, driver=client, sleeve_sensor=front),
    ]
    with (
      patch.object(
        client.io.introspection,
        "get_object",
        new=AsyncMock(side_effect=lambda addr: ObjectInfo("SDrive", "", 2, 0, addr)),
      ),
      patch.object(
        client.io.introspection,
        "get_method",
        new=AsyncMock(side_effect=lambda addr, index: tables[addr][index]),
      ) as get_method,
    ):
      self.assertEqual(
        await asyncio.wait_for(channels.sense_tip_presence(), timeout=1), [True, False]
      )
      self.assertEqual(
        await asyncio.wait_for(channels.sense_tip_presence(), timeout=1), [True, False]
      )
      self.assertEqual(get_method.await_count, 4)
    self.assertEqual(len(io.writes), 4)

  async def test_tip_presence_requires_one_named_method_before_querying(self):
    """Absent or ambiguous names cannot fall back to numeric sensor IDs."""
    for methods in (
      [MethodInfo(3, 0, 15, "UnrelatedMethod")],
      [MethodInfo(1, 0, 15, "GetTipPresent"), MethodInfo(2, 0, 15, "GetTipPresent")],
    ):
      with self.subTest(methods=methods):
        client, io = self.make_driver()
        channels = Pipettes(client)
        channels.channels = [
          PipetteChannel(index=0, driver=client, sleeve_sensor=Address(1, 236, 514))
        ]
        with patch.object(
          client.io.introspection,
          "ensure_method_table",
          new=AsyncMock(return_value=methods),
        ):
          with self.assertRaisesRegex(RuntimeError, "GetTipPresent"):
            await asyncio.wait_for(channels.sense_tip_presence(), timeout=1)
        self.assertEqual(io.writes, [])

  async def test_prep_session_logs_each_request_and_its_answer(self):
    """The request, the object it went to, and the decoded answer or the firmware error, at DEBUG level."""
    driver = PrepDriver(deck=PrepDeck(), host="memory-only", port=0)
    io = self.start_session(driver, Address(1, 1, 257), session_type=_PrepTCPSession)
    self.addAsyncCleanup(driver.io.stop)
    answers = [
      (Hoi2Action.STATUS_RESPONSE, HoiParams().add(180.0, F32).build()),
      (
        Hoi2Action.COMMAND_EXCEPTION,
        HoiParams().add("0x0001.0x0001.0x0101:0x01,0x0006,0x0F08", Str).build(),
      ),
    ]

    async def respond(request: HarpPacket) -> None:
      """Answer the traverse height, then refuse the park."""
      action, params = answers.pop(0)
      io.feed(_response(sequence=request.seq, action=action, params=params))

    io.on_write = respond
    with self.assertLogs("pylabrobot.hamilton.prep.driver.master", level=logging.DEBUG) as logs:
      await driver.send_command(C.PrepGetDefaultTraverseHeight())
      with self.assertRaises(HoiError):
        await driver.send_command(C.PrepPark())
    lines = [record.getMessage() for record in logs.records]
    self.assertIn("write: PrepGetDefaultTraverseHeight(", lines[0])
    self.assertIn("to 1:1:257", lines[0])
    self.assertIn("read: PrepGetDefaultTraverseHeight.Response(value=180.0)", lines[1])
    self.assertIn("write: PrepPark(", lines[2])
    self.assertIn("read: error: 0x0F08", lines[3])

  async def test_prep_session_logs_probe_answers_as_values(self):
    """A probe by ids decodes nothing itself: its answer is logged as values, structures opened, not as bytes."""
    driver = PrepDriver(deck=PrepDeck(), host="memory-only", port=0)
    address = Address(1, 1, 257)
    io = self.start_session(driver, address, session_type=_PrepTCPSession)
    self.addAsyncCleanup(driver.io.stop)
    inner = HoiParams().add(1.5, F32).build()
    params = HoiParams().add(180.0, F32).build() + bytes([30, 0, len(inner), 0]) + inner

    async def respond(request: HarpPacket) -> None:
      io.feed(_response(sequence=request.seq, action=Hoi2Action.STATUS_RESPONSE, params=params))

    io.on_write = respond
    with self.assertLogs("pylabrobot.hamilton.prep.driver.master", level=logging.DEBUG) as logs:
      await driver.send_command(C.PrepProbeRequest(dest=address, command_id=5, interface_id=1))
    lines = [record.getMessage() for record in logs.records]
    self.assertIn("read: [180.0, [1.5]]", lines[1])

  async def test_reusable_request_rebinds_after_reconnection(self):
    client, io = self.make_driver()
    command = C.PrepGetDefaultTraverseHeight()
    for address in (Address(1, 1, 257), Address(1, 2, 300)):
      if address.node == 2:
        await client.io.stop()
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
      responses = await asyncio.gather(client.send_command(command), client.send_command(command))
      self.assertEqual(responses, [C.PrepGetDefaultTraverseHeight.Response(180.0)] * 2)
      self.assertEqual([request.seq for request in io.writes], [1, 2])
      self.assertEqual(command, C.PrepGetDefaultTraverseHeight())
    with self.assertRaises(FrozenInstanceError):
      command.dest = Address(1, 1, 999)  # type: ignore[misc]

  async def test_identity_query_uses_runtime_interface_and_method(self):
    client, io = self.make_driver()

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
    self.assertEqual(await client.request_firmware_string(Address(1, 1, 257), 9), "PREP123")

  async def test_execute_raises_firmware_error_and_exchange_preserves_frame(self):
    client, io = self.make_driver()
    payload = HoiParams().add("0x0001.0x0001.0x0101:0x01,0x0006,0x0F08", Str).build()

    async def respond(request: HarpPacket) -> None:
      """Return an error without accepting any diagnostic round trips."""
      io.feed(_response(sequence=request.seq, action=Hoi2Action.COMMAND_EXCEPTION, params=payload))

    io.on_write = respond
    with self.assertRaises(HoiError) as caught:
      await client.send_command(C.PrepPark())
    self.assertEqual(caught.exception.raw_response, payload)
    self.assertEqual(len(io.writes), 1)
    raw = await client.exchange(C.PrepPark())
    self.assertEqual(raw.hoi.params, payload)
    self.assertEqual(raw.hoi.action_code, Hoi2Action.COMMAND_EXCEPTION)
    self.assertEqual(client.io.connection_info.state, SessionState.READY)

  async def test_errors_follow_selected_channels_instead_of_firmware_ordinals(self):
    client, io = self.make_driver()
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
      await client.send_command(command)
    self.assertEqual(set(caught.exception.errors), {1})

  async def test_cancellation_prevents_further_prep_queries(self):
    client, io = self.make_driver()
    task = asyncio.create_task(client.send_command(C.PrepGetDefaultTraverseHeight()))
    await self.wait_until(lambda: len(io.writes) == 1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(client.io.connection_info.state, SessionState.UNCERTAIN)
    with self.assertRaises(ConnectionError):
      await client.send_command(C.PrepPark())
    self.assertEqual(len(io.writes), 1)

  async def test_failed_prep_identity_check_closes_the_session(self):
    client, io = self.make_driver()
    with (
      patch.object(HamiltonTCPClient, "setup", new=AsyncMock()),
      patch.object(client, "request_root_name", new=AsyncMock(return_value="WrongRoot")),
    ):
      with self.assertRaisesRegex(RuntimeError, "Wrong instrument"):
        await client._open()
    self.assertTrue(io.closed)
    self.assertEqual(client.io.connection_info.state, SessionState.CLOSED)

  async def test_simulation_decodes_queries_and_invalidates_retained_discovery(self):
    client = PrepSimulationDriver(deck=PrepDeck())
    await client._open()
    self.addAsyncCleanup(client._close)
    intro = client.io.introspection
    address = await client.resolve_path(PIPETTOR_OBJECT_PATH)
    result = await client.send_command(C.PrepGetIsInitialized(dest=client.mlprep_address))
    self.assertIsInstance(result, C.PrepGetIsInitialized.Response)
    frame = await client.exchange(C.PrepPark())
    self.assertEqual(frame.harp.action_code, 4)
    await client._close()
    await client._open()
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
    client, io = self.make_driver()
    client.io.registry.register(
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
    bounds = await Pipettes(client).request_channel_bounds()
    # The device answers in float32; the read gives back 0.01 mm.
    self.assertEqual(
      bounds,
      [
        dict(
          x_min=0.51,
          x_max=299.51,
          y_min=0,
          y_max=385,
          z_min=19.5,
          z_max=167.5,
        ),
        dict(
          x_min=0.51,
          x_max=299.51,
          y_min=-9,
          y_max=376,
          z_min=19.5,
          z_max=167.5,
        ),
      ],
    )

  async def test_reconnection_during_path_resolution_cannot_send_an_old_address(self):
    for raw in (False, True):
      client, io = self.make_driver()
      fresh_sockets: list[_MemorySocket] = []

      async def resolve(path: str) -> Address:
        """Replace the session while a firmware-path lookup is in flight."""
        await client.io.stop()
        fresh = self.start_session(client, Address(1, 2, 300))
        fresh.on_write = AsyncMock(side_effect=AssertionError("stale address reached new session"))
        fresh_sockets.append(fresh)
        return Address(1, 1, 257)

      client.resolve_path = resolve  # type: ignore[method-assign]
      with self.assertRaises(ConnectionError):
        if raw:
          await client.exchange(C.PrepPark())
        else:
          await client.send_command(C.PrepPark())
      self.assertTrue(io.closed)
      self.assertEqual(fresh_sockets[0].writes, [])
      self.assertEqual(client.io.connection_info.state, SessionState.READY)
