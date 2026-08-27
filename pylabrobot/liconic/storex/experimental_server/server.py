"""Async gRPC server for a LiCONiC StoreX."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Literal, NoReturn, Optional, TypeVar, cast

try:
  import grpc  # type: ignore[import-untyped]
except ImportError as error:  # pragma: no cover - depends on optional installation
  raise ImportError(
    "The StoreX server requires the 'liconic-server' optional dependencies. "
    "Install PyLabRobot with `pip install 'PyLabRobot[liconic-server]'`."
  ) from error

from pylabrobot.liconic.storex.storex import NoFreeSiteError, StorageSite, StoreX
from pylabrobot.resources import Plate, PlateHolder, Resource, ResourceNotFoundError

try:
  from . import storex_pb2, storex_pb2_grpc
except ImportError as error:  # pragma: no cover - depends on optional installation
  raise ImportError(
    "The StoreX server requires protobuf. "
    "Install PyLabRobot with `pip install 'PyLabRobot[liconic-server]'`."
  ) from error


logger = logging.getLogger(__name__)
_T = TypeVar("_T")
OperationName = Literal["initialize", "fetch_plate", "store_plate"]


async def _finish_hardware_operation(awaitable: Awaitable[_T]) -> _T:
  """Keep a hardware operation and its lock alive after caller cancellation."""
  future: asyncio.Future[_T] = asyncio.ensure_future(awaitable)
  try:
    return await asyncio.shield(future)
  except asyncio.CancelledError:
    try:
      await future
    except Exception:
      logger.exception("StoreX operation failed after its RPC was cancelled")
    raise


@dataclass(frozen=True)
class _ActiveTransfer:
  transfer_id: str
  plate: Plate
  resource_json: str


@dataclass(frozen=True)
class _ActiveOperation:
  name: OperationName
  plate_name: Optional[str] = None


class _ServiceError(Exception):
  def __init__(self, code: grpc.StatusCode, details: str) -> None:
    super().__init__(details)
    self.code = code
    self.details = details


class StoreXService(storex_pb2_grpc.StoreXServiceServicer):
  """Translate StoreX RPCs into serialized operations on one device.

  The StoreX resource tree and the caller's workcell resource tree are separate.
  Claiming a tray plate removes it from the StoreX tree and returns a serialized
  copy. The tray remains reserved until the caller completes or releases the
  transfer.
  """

  def __init__(self, storex: StoreX) -> None:
    self.storex = storex
    self._lock = asyncio.Lock()
    self._active_transfer: Optional[_ActiveTransfer] = None
    self._active_operation: Optional[_ActiveOperation] = None
    self._finished_transfers: OrderedDict[str, Literal["completed", "released"]] = OrderedDict()

  @staticmethod
  def _serialize_resource(resource: Resource) -> str:
    return json.dumps(resource.serialize(), separators=(",", ":"), sort_keys=True)

  @classmethod
  def _plate_snapshot(cls, plate: Plate, resource_json: Optional[str] = None):
    return storex_pb2.PlateSnapshot(
      name=plate.name,
      resource_json=resource_json or cls._serialize_resource(plate),
    )

  def _remember_transfer(self, transfer_id: str, outcome: Literal["completed", "released"]) -> None:
    self._finished_transfers[transfer_id] = outcome
    self._finished_transfers.move_to_end(transfer_id)
    while len(self._finished_transfers) > 128:
      self._finished_transfers.popitem(last=False)

  def _require_no_active_transfer(self) -> None:
    if self._active_transfer is not None:
      raise _ServiceError(
        grpc.StatusCode.FAILED_PRECONDITION,
        f"Loading tray is reserved by transfer {self._active_transfer.transfer_id!r}",
      )

  async def _run_hardware_operation(
    self,
    name: OperationName,
    awaitable: Awaitable[_T],
    plate_name: Optional[str] = None,
  ) -> _T:
    """Expose one in-flight hardware operation while retaining cancellation safety."""
    if self._active_operation is not None:
      raise RuntimeError(f"StoreX operation {self._active_operation.name!r} is already active")
    self._active_operation = _ActiveOperation(name=name, plate_name=plate_name)
    try:
      return await _finish_hardware_operation(awaitable)
    finally:
      self._active_operation = None

  def _site_from_address(self, address) -> PlateHolder:
    if address.cassette < 1 or address.cassette > len(self.storex.racks):
      raise _ServiceError(
        grpc.StatusCode.INVALID_ARGUMENT,
        f"Cassette must be between 1 and {len(self.storex.racks)}",
      )
    rack = self.storex.racks[address.cassette - 1]
    if address.position < 1:
      raise _ServiceError(
        grpc.StatusCode.INVALID_ARGUMENT,
        "Position must be at least 1",
      )
    try:
      return cast(PlateHolder, rack.sites[address.position - 1])
    except KeyError:
      raise _ServiceError(
        grpc.StatusCode.INVALID_ARGUMENT,
        f"Cassette {address.cassette} has no position {address.position}",
      )

  def _address_from_site(self, site: PlateHolder):
    cassette, position = self.storex._site_to_address(site)
    return storex_pb2.SiteAddress(cassette=cassette, position=position)

  async def _abort(self, context: grpc.aio.ServicerContext, error: Exception) -> NoReturn:
    if isinstance(error, _ServiceError):
      code = error.code
      details = error.details
    elif isinstance(error, ResourceNotFoundError):
      code = grpc.StatusCode.NOT_FOUND
      details = str(error)
    elif isinstance(error, NoFreeSiteError):
      code = grpc.StatusCode.RESOURCE_EXHAUSTED
      details = str(error)
    elif isinstance(error, NotImplementedError):
      code = grpc.StatusCode.UNIMPLEMENTED
      details = str(error)
    elif isinstance(error, TimeoutError):
      code = grpc.StatusCode.DEADLINE_EXCEEDED
      details = str(error)
    elif isinstance(error, (ValueError, TypeError)):
      code = grpc.StatusCode.FAILED_PRECONDITION
      details = str(error)
    else:
      code = grpc.StatusCode.INTERNAL
      details = str(error)
    await context.abort(code, details)
    raise AssertionError("gRPC context.abort returned")

  async def GetState(self, request, context):
    del request, context
    response = storex_pb2.StoreXState(storex_resource_json=self._serialize_resource(self.storex))
    if self._active_transfer is not None:
      response.active_transfer.transfer_id = self._active_transfer.transfer_id
      response.active_transfer.plate_name = self._active_transfer.plate.name
    if self._active_operation is not None:
      response.active_operation.name = self._active_operation.name
      if self._active_operation.plate_name is not None:
        response.active_operation.plate_name = self._active_operation.plate_name
    return response

  async def Initialize(self, request, context):
    del request
    try:
      async with self._lock:
        self._require_no_active_transfer()
        if self.storex.loading_tray.resource is not None:
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "Cannot initialize while the loading tray owns a plate",
          )
        if await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "Cannot initialize while the loading-tray sensor detects a plate",
          )
        await self._run_hardware_operation("initialize", self.storex.initialize())
        return storex_pb2.Empty()
    except Exception as error:
      await self._abort(context, error)

  async def FetchPlate(self, request, context):
    try:
      if not request.plate_name:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "plate_name is required")
      async with self._lock:
        self._require_no_active_transfer()
        tray_plate = self.storex.loading_tray.resource
        if tray_plate is not None:
          if isinstance(tray_plate, Plate) and tray_plate.name == request.plate_name:
            return self._plate_snapshot(tray_plate)
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Loading tray is occupied by {tray_plate.name!r}",
          )
        plate = await self._run_hardware_operation(
          "fetch_plate",
          self.storex.fetch_plate_to_loading_tray(
            request.plate_name,
            read_barcode=request.read_barcode,
          ),
          plate_name=request.plate_name,
        )
        return self._plate_snapshot(plate)
    except Exception as error:
      await self._abort(context, error)

  async def ClaimTrayPlate(self, request, context):
    try:
      if not request.transfer_id:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "transfer_id is required")
      async with self._lock:
        if self._active_transfer is not None:
          if self._active_transfer.transfer_id == request.transfer_id:
            return storex_pb2.ClaimTrayPlateResponse(
              transfer_id=request.transfer_id,
              plate=self._plate_snapshot(
                self._active_transfer.plate,
                self._active_transfer.resource_json,
              ),
            )
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Loading tray is reserved by transfer {self._active_transfer.transfer_id!r}",
          )
        if request.transfer_id in self._finished_transfers:
          raise _ServiceError(
            grpc.StatusCode.ALREADY_EXISTS,
            f"Transfer {request.transfer_id!r} is already finished",
          )
        plate = self.storex.loading_tray.resource
        if not isinstance(plate, Plate):
          raise _ServiceError(grpc.StatusCode.NOT_FOUND, "No plate is registered on the tray")
        if not await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "The loading-tray sensor does not detect the registered plate",
          )
        resource_json = self._serialize_resource(plate)
        plate.unassign()
        self._active_transfer = _ActiveTransfer(
          transfer_id=request.transfer_id,
          plate=plate,
          resource_json=resource_json,
        )
        return storex_pb2.ClaimTrayPlateResponse(
          transfer_id=request.transfer_id,
          plate=self._plate_snapshot(plate, resource_json),
        )
    except Exception as error:
      await self._abort(context, error)

  async def CompleteTransfer(self, request, context):
    try:
      if not request.transfer_id:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "transfer_id is required")
      async with self._lock:
        outcome = self._finished_transfers.get(request.transfer_id)
        if outcome == "completed":
          return storex_pb2.Empty()
        if outcome == "released":
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Transfer {request.transfer_id!r} was released",
          )
        transfer = self._active_transfer
        if transfer is None or transfer.transfer_id != request.transfer_id:
          raise _ServiceError(grpc.StatusCode.NOT_FOUND, "Active transfer not found")
        if await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "The loading-tray sensor still detects a plate",
          )
        self._active_transfer = None
        self._remember_transfer(request.transfer_id, "completed")
        return storex_pb2.Empty()
    except Exception as error:
      await self._abort(context, error)

  async def ReleaseTransfer(self, request, context):
    try:
      if not request.transfer_id:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "transfer_id is required")
      async with self._lock:
        outcome = self._finished_transfers.get(request.transfer_id)
        if outcome == "released":
          return storex_pb2.Empty()
        if outcome == "completed":
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Transfer {request.transfer_id!r} was completed",
          )
        transfer = self._active_transfer
        if transfer is None or transfer.transfer_id != request.transfer_id:
          raise _ServiceError(grpc.StatusCode.NOT_FOUND, "Active transfer not found")
        if not await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "Cannot release ownership because the tray sensor does not detect the plate",
          )
        if self.storex.loading_tray.resource is not None:
          raise _ServiceError(
            grpc.StatusCode.INTERNAL,
            "The loading tray acquired another resource during an active transfer",
          )
        self.storex.loading_tray.assign_child_resource(transfer.plate)
        self._active_transfer = None
        self._remember_transfer(request.transfer_id, "released")
        return storex_pb2.Empty()
    except Exception as error:
      await self._abort(context, error)

  async def RegisterTrayPlate(self, request, context):
    try:
      if not request.resource_json:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "resource_json is required")
      try:
        resource_data = json.loads(request.resource_json)
        resource = Resource.deserialize(resource_data)
      except (KeyError, TypeError, ValueError) as error:
        raise _ServiceError(
          grpc.StatusCode.INVALID_ARGUMENT,
          f"Invalid serialized resource: {error}",
        )
      if not isinstance(resource, Plate):
        raise _ServiceError(
          grpc.StatusCode.INVALID_ARGUMENT,
          "resource_json must contain a Plate",
        )

      async with self._lock:
        self._require_no_active_transfer()
        tray_plate = self.storex.loading_tray.resource
        if tray_plate is not None:
          if (
            isinstance(tray_plate, Plate)
            and tray_plate.name == resource.name
            and self._serialize_resource(tray_plate) == self._serialize_resource(resource)
          ):
            return self._plate_snapshot(tray_plate)
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Loading tray is already occupied by {tray_plate.name!r}",
          )
        if not await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "The loading-tray sensor does not detect a plate",
          )
        self.storex.loading_tray.assign_child_resource(resource)
        return self._plate_snapshot(resource)
    except Exception as error:
      await self._abort(context, error)

  async def StoreTrayPlate(self, request, context):
    try:
      if not request.plate_name:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "plate_name is required")
      destination_kind = request.WhichOneof("destination")
      if destination_kind is None:
        raise _ServiceError(grpc.StatusCode.INVALID_ARGUMENT, "destination is required")

      async with self._lock:
        self._require_no_active_transfer()
        tray_plate = self.storex.loading_tray.resource
        if tray_plate is None:
          try:
            existing_site = self.storex.get_site_by_plate_name(request.plate_name)
          except ResourceNotFoundError:
            raise _ServiceError(
              grpc.StatusCode.FAILED_PRECONDITION,
              f"Plate {request.plate_name!r} is neither on the tray nor in storage",
            )
          if destination_kind == "site" and existing_site is not self._site_from_address(
            request.site
          ):
            raise _ServiceError(
              grpc.StatusCode.FAILED_PRECONDITION,
              f"Plate {request.plate_name!r} is already stored at a different site",
            )
          existing_plate = cast(Plate, existing_site.resource)
          return storex_pb2.StoredPlate(
            plate=self._plate_snapshot(existing_plate),
            site=self._address_from_site(existing_site),
          )
        if not isinstance(tray_plate, Plate) or tray_plate.name != request.plate_name:
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"Loading tray does not contain plate {request.plate_name!r}",
          )
        if not await self.storex.request_transfer_sensor():
          raise _ServiceError(
            grpc.StatusCode.FAILED_PRECONDITION,
            "The loading-tray sensor does not detect the registered plate",
          )

        if destination_kind == "site":
          destination: StorageSite = self._site_from_address(request.site)
        elif destination_kind == "random_fit":
          destination = "random"
        else:
          destination = "smallest"
        plate = await self._run_hardware_operation(
          "store_plate",
          self.storex.take_in_plate(
            destination,
            read_barcode=request.read_barcode,
          ),
          plate_name=request.plate_name,
        )
        site = self.storex.get_site_by_plate_name(plate.name)
        return storex_pb2.StoredPlate(
          plate=self._plate_snapshot(plate),
          site=self._address_from_site(site),
        )
    except Exception as error:
      await self._abort(context, error)


class StoreXServer:
  """Own one StoreX connection and expose it through an async gRPC server."""

  def __init__(
    self,
    storex: StoreX,
    host: str = "127.0.0.1",
    port: int = 50051,
  ) -> None:
    self.storex = storex
    self.host = host
    self._server = grpc.aio.server()
    self.service = StoreXService(storex)
    storex_pb2_grpc.add_StoreXServiceServicer_to_server(self.service, self._server)
    self.port = self._server.add_insecure_port(f"{host}:{port}")
    if self.port == 0:
      raise RuntimeError(f"Could not bind StoreX gRPC server to {host}:{port}")
    self._started = False

  async def setup(self) -> None:
    """Connect to the StoreX, then start accepting RPCs."""
    if self._started:
      return
    await self.storex.setup()
    try:
      await self._server.start()
    except Exception:
      await self.storex.stop()
      raise
    self._started = True

  async def stop(self, grace: float = 5.0) -> None:
    """Stop accepting RPCs, drain calls, and close the StoreX connection."""
    if not self._started:
      return
    try:
      await self._server.stop(grace)
    finally:
      await self.storex.stop()
      self._started = False

  async def wait_for_termination(self) -> None:
    await self._server.wait_for_termination()
