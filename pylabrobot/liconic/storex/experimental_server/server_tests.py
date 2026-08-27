"""Tests for the experimental StoreX gRPC server."""

import asyncio
import json
import unittest
from typing import NoReturn
from unittest.mock import AsyncMock

import grpc  # type: ignore[import-untyped]

from pylabrobot.liconic.storex.racks import storex_rack_17mm_22
from pylabrobot.liconic.storex.storex import StoreX
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb

from . import storex_pb2, storex_pb2_grpc
from .server import StoreXServer, StoreXService


class RpcAbort(Exception):
  def __init__(self, code: grpc.StatusCode, details: str) -> None:
    super().__init__(details)
    self.code = code
    self.details = details


class FakeContext:
  async def abort(self, code: grpc.StatusCode, details: str) -> NoReturn:
    raise RpcAbort(code, details)


def make_storex() -> StoreX:
  return StoreX(
    name="storex",
    model="STX44_IC",
    port="/dev/null",
    racks=[storex_rack_17mm_22("cassette_1")],
    loading_tray_location=Coordinate.zero(),
  )


def make_plate(name: str = "plate") -> Plate:
  return cor_96_wellplate_360uL_Fb(name=name)


class TestTransferOwnership(unittest.IsolatedAsyncioTestCase):
  async def test_claim_moves_ownership_to_distinct_workcell_resource(self) -> None:
    storex = make_storex()
    server_plate = make_plate("assay_plate")
    storex.racks[0].sites[0].assign_child_resource(server_plate)

    async def fetch(plate_name: str, read_barcode: bool = False) -> Plate:
      del read_barcode
      site = storex.get_site_by_plate_name(plate_name)
      plate = site.resource
      assert isinstance(plate, Plate)
      plate.unassign()
      storex.loading_tray.assign_child_resource(plate)
      return plate

    storex.fetch_plate_to_loading_tray = AsyncMock(side_effect=fetch)  # type: ignore[method-assign]
    storex.request_transfer_sensor = AsyncMock(side_effect=[True, False])  # type: ignore[method-assign]
    service = StoreXService(storex)
    context = FakeContext()

    await service.FetchPlate(
      storex_pb2.FetchPlateRequest(plate_name="assay_plate"),
      context,
    )
    claim = await service.ClaimTrayPlate(
      storex_pb2.ClaimTrayPlateRequest(transfer_id="transfer-1"),
      context,
    )

    self.assertIsNone(server_plate.parent)
    self.assertIsNone(storex.loading_tray.resource)

    local_plate = Resource.deserialize(json.loads(claim.plate.resource_json))
    self.assertIsInstance(local_plate, Plate)
    self.assertIsNot(local_plate, server_plate)
    handoff = PlateHolder("storex_handoff", 127.76, 85.48, 0, pedestal_size_z=0)
    handoff.assign_child_resource(local_plate)

    await service.CompleteTransfer(
      storex_pb2.TransferRequest(transfer_id="transfer-1"),
      context,
    )
    state = await service.GetState(storex_pb2.Empty(), context)

    self.assertIs(handoff.resource, local_plate)
    self.assertFalse(state.HasField("active_transfer"))

  async def test_claim_retry_returns_same_transfer(self) -> None:
    storex = make_storex()
    plate = make_plate()
    storex.loading_tray.assign_child_resource(plate)
    storex.request_transfer_sensor = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service = StoreXService(storex)
    request = storex_pb2.ClaimTrayPlateRequest(transfer_id="transfer-1")

    first = await service.ClaimTrayPlate(request, FakeContext())
    second = await service.ClaimTrayPlate(request, FakeContext())

    self.assertEqual(first, second)
    storex.request_transfer_sensor.assert_awaited_once()

  async def test_release_restores_storex_ownership(self) -> None:
    storex = make_storex()
    plate = make_plate()
    storex.loading_tray.assign_child_resource(plate)
    storex.request_transfer_sensor = AsyncMock(side_effect=[True, True])  # type: ignore[method-assign]
    service = StoreXService(storex)
    context = FakeContext()
    request = storex_pb2.TransferRequest(transfer_id="transfer-1")

    await service.ClaimTrayPlate(
      storex_pb2.ClaimTrayPlateRequest(transfer_id="transfer-1"),
      context,
    )
    await service.ReleaseTransfer(request, context)
    await service.ReleaseTransfer(request, context)

    self.assertIs(storex.loading_tray.resource, plate)
    self.assertEqual(storex.request_transfer_sensor.await_count, 2)

  async def test_claim_without_plate_returns_not_found(self) -> None:
    service = StoreXService(make_storex())

    with self.assertRaises(RpcAbort) as error:
      await service.ClaimTrayPlate(
        storex_pb2.ClaimTrayPlateRequest(transfer_id="transfer-1"),
        FakeContext(),
      )

    self.assertEqual(error.exception.code, grpc.StatusCode.NOT_FOUND)

  async def test_cancelled_fetch_finishes_before_releasing_device_lock(self) -> None:
    storex = make_storex()
    plate = make_plate()
    storex.racks[0].sites[0].assign_child_resource(plate)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fetch(plate_name: str, read_barcode: bool = False) -> Plate:
      del read_barcode
      started.set()
      await finish.wait()
      site = storex.get_site_by_plate_name(plate_name)
      fetched_plate = site.resource
      assert isinstance(fetched_plate, Plate)
      fetched_plate.unassign()
      storex.loading_tray.assign_child_resource(fetched_plate)
      return fetched_plate

    storex.fetch_plate_to_loading_tray = AsyncMock(side_effect=fetch)  # type: ignore[method-assign]
    service = StoreXService(storex)
    rpc = asyncio.create_task(
      service.FetchPlate(
        storex_pb2.FetchPlateRequest(plate_name="plate"),
        FakeContext(),
      )
    )
    await started.wait()

    rpc.cancel()
    await asyncio.sleep(0)
    self.assertFalse(rpc.done())
    finish.set()
    with self.assertRaises(asyncio.CancelledError):
      await rpc

    self.assertIs(storex.loading_tray.resource, plate)


class TestStorePlate(unittest.IsolatedAsyncioTestCase):
  async def test_register_and_store_creates_server_owned_plate(self) -> None:
    storex = make_storex()
    local_plate = make_plate("incoming")

    async def take_in(site, read_barcode: bool = False) -> Plate:
      del read_barcode
      plate = storex.loading_tray.resource
      assert isinstance(plate, Plate)
      plate.unassign()
      site.assign_child_resource(plate)
      return plate

    storex.request_transfer_sensor = AsyncMock(return_value=True)  # type: ignore[method-assign]
    storex.take_in_plate = AsyncMock(side_effect=take_in)  # type: ignore[method-assign]
    service = StoreXService(storex)
    context = FakeContext()

    await service.RegisterTrayPlate(
      storex_pb2.RegisterTrayPlateRequest(
        resource_json=json.dumps(local_plate.serialize()),
      ),
      context,
    )
    result = await service.StoreTrayPlate(
      storex_pb2.StoreTrayPlateRequest(
        plate_name="incoming",
        site=storex_pb2.SiteAddress(cassette=1, position=2),
      ),
      context,
    )

    stored_plate = storex.racks[0].sites[1].resource
    self.assertIsInstance(stored_plate, Plate)
    self.assertIsNot(stored_plate, local_plate)
    self.assertEqual(result.site.cassette, 1)
    self.assertEqual(result.site.position, 2)


class TestGrpcLifecycle(unittest.IsolatedAsyncioTestCase):
  async def test_get_state_reports_initialization_without_waiting_for_it(self) -> None:
    storex = make_storex()
    storex.request_transfer_sensor = AsyncMock(return_value=False)  # type: ignore[method-assign]
    started = asyncio.Event()
    finish = asyncio.Event()

    async def initialize() -> None:
      started.set()
      await finish.wait()

    storex.initialize = AsyncMock(side_effect=initialize)  # type: ignore[method-assign]
    service = StoreXService(storex)
    initialization = asyncio.create_task(service.Initialize(storex_pb2.Empty(), FakeContext()))
    await started.wait()

    state = await asyncio.wait_for(
      service.GetState(storex_pb2.Empty(), FakeContext()),
      timeout=0.1,
    )

    self.assertTrue(state.HasField("active_operation"))
    self.assertEqual(state.active_operation.name, "initialize")
    self.assertFalse(state.active_operation.HasField("plate_name"))
    finish.set()
    await initialization
    state = await service.GetState(storex_pb2.Empty(), FakeContext())
    self.assertFalse(state.HasField("active_operation"))

  async def test_in_process_client_can_read_state(self) -> None:
    storex = make_storex()
    storex.setup = AsyncMock()  # type: ignore[method-assign]
    storex.stop = AsyncMock()  # type: ignore[method-assign]
    server = StoreXServer(storex, port=0)
    await server.setup()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.port}")
    try:
      stub = storex_pb2_grpc.StoreXServiceStub(channel)
      state = await stub.GetState(storex_pb2.Empty())
      serialized = json.loads(state.storex_resource_json)
      self.assertEqual(serialized["name"], "storex")
    finally:
      await channel.close()
      await server.stop(grace=0)

    storex.setup.assert_awaited_once()
    storex.stop.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
