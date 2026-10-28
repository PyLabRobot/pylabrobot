"""The in-memory store: an `Engine` served over Connect, as `ResourceService` and
`ResourcePrimitivesService`.

Requests run one at a time. Each one that changes anything moves the store's revision on, and its
changes are kept as events for `Watch`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TypeVar

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from google.protobuf.timestamp_pb2 import Timestamp
from protobuf.wkt import Any as AnyMessage

from pylabrobot.resources.server import commands, library
from pylabrobot.resources.server import geometry as g
from pylabrobot.resources.server.engine import Engine, volume_issue
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tree import Node, to_coordinate
from pylabrobot.resources.server.v1 import primitives_pb2 as ppb
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.server.v1.primitives_connect import (
  ResourcePrimitivesService,
  ResourcePrimitivesServiceASGIApplication,
)
from pylabrobot.resources.server.v1.server_connect import (
  ResourceService,
  ResourceServiceASGIApplication,
)

T = TypeVar("T")

_CODES = {
  pb.PROBLEM_UNSPECIFIED: Code.INVALID_ARGUMENT,
  pb.PROBLEM_NOT_FOUND: Code.NOT_FOUND,
  pb.PROBLEM_NAME_CONFLICT: Code.ALREADY_EXISTS,
  pb.PROBLEM_NO_LOCATION: Code.FAILED_PRECONDITION,
  pb.PROBLEM_NOT_IN_FRAME: Code.INVALID_ARGUMENT,
  pb.PROBLEM_WRONG_KIND: Code.INVALID_ARGUMENT,
  pb.PROBLEM_CANNOT_DROP_HERE: Code.FAILED_PRECONDITION,
  pb.PROBLEM_LOCKED: Code.ABORTED,
  pb.PROBLEM_NO_HEIGHT_VOLUME_DATA: Code.FAILED_PRECONDITION,
  pb.PROBLEM_OUTSIDE_HEIGHT_VOLUME_DATA: Code.OUT_OF_RANGE,
  pb.PROBLEM_OUT_OF_RANGE: Code.OUT_OF_RANGE,
}
ISSUE_TYPE = "pylabrobot.resources.server.v1.Issue"


def connect_error(error: ServerError) -> ConnectError:
  """A store's refusal as a Connect error, its `Issue` attached as a detail."""
  issue = pb.Issue(problem=error.problem, message=str(error), item=-1)
  detail = AnyMessage(type_url=f"type.googleapis.com/{ISSUE_TYPE}", value=issue.SerializeToString())
  return ConnectError(_CODES.get(error.problem, Code.FAILED_PRECONDITION), str(error), [detail])


@dataclass
class _Event:
  message: spb.ResourceEvent
  roots: Set[bytes]


class MemoryStore(ResourceService, ResourcePrimitivesService):
  """A resource store held in memory, for one process; the default store of PLR. It starts with
  PLR's labware models put in, each read from its file when first asked for."""

  def __init__(self, engine: Optional[Engine] = None):
    self.engine = engine or Engine(library=library.LIBRARY)
    self._lock = asyncio.Lock()
    self._changed: Optional[asyncio.Condition] = None
    self._events: List[_Event] = []

  def _condition(self) -> asyncio.Condition:
    if self._changed is None:
      self._changed = asyncio.Condition()
    return self._changed

  async def _call(self, work: Callable[[], T]) -> T:
    """Run one request against the engine, all or none, and publish what it changed."""
    async with self._lock:
      try:
        result = self.engine.run(work)
      except ServerError as refused:
        raise connect_error(refused) from refused
      self._publish()
    if self._events:
      condition = self._condition()
      async with condition:
        condition.notify_all()
    return result  # type: ignore[return-value]

  def _publish(self) -> None:
    changes = self.engine.drain_changes()
    if changes is None:
      return
    revision = self.engine.revision
    stamp = _timestamp()
    for node in changes.created:
      self._events.append(
        _Event(
          spb.ResourceEvent(revision=revision, at=stamp, created=self.engine.node_message(node)),
          {node.root().id} | changes.roots,
        )
      )
    for node in changes.updated:
      self._events.append(
        _Event(
          spb.ResourceEvent(
            revision=revision,
            at=stamp,
            updated=self.engine.node_message(node),
            changed=changes.aspects[node.id],
          ),
          {node.root().id} | changes.roots,
        )
      )
    for node_id in changes.deleted:
      self._events.append(
        _Event(spb.ResourceEvent(revision=revision, at=stamp, deleted=node_id), set(changes.roots))
      )
    for operation in changes.operations:
      self._events.append(
        _Event(
          spb.ResourceEvent(revision=revision, at=stamp, operation=operation),
          {operation.root_id},
        )
      )

  def _nodes(self, nodes: List[Node]) -> List[pb.ResourceNode]:
    seen: Set[bytes] = set()
    found = []
    for node in nodes:
      if node.id in self.engine.nodes and node.id not in seen:
        seen.add(node.id)
        found.append(self.engine.node_message(node))
    return found

  # -- the store ---------------------------------------------------------------------------------

  async def get_server_info(
    self, request: spb.GetServerInfoRequest, ctx: RequestContext
  ) -> spb.ServerInfo:
    return await self._call(self.engine.info)

  async def acquire_lock(self, request: spb.AcquireLockRequest, ctx: RequestContext) -> spb.Lock:
    wait = request.wait.ToTimedelta().total_seconds() if request.HasField("wait") else 0.0
    deadline = asyncio.get_running_loop().time() + wait
    while True:
      lock = await self._call(
        lambda: self.engine.acquire_lock(self.engine.resolve(request.resource), request.holder)
      )
      if lock is not None:
        return lock
      remaining = deadline - asyncio.get_running_loop().time()
      if remaining <= 0:
        raise ConnectError(Code.ABORTED, await self._call(lambda: self._overlap(request.resource)))
      condition = self._condition()
      async with condition:
        try:
          await asyncio.wait_for(condition.wait(), timeout=remaining)
        except asyncio.TimeoutError:
          pass

  def _overlap(self, ref: pb.ResourceRef) -> str:
    """Why a lock on `ref` cannot be had now: the lock it would overlap."""
    node = self.engine.resolve(ref)
    overlap = self.engine.overlapping_lock(node)
    if overlap is None:
      return f"'{node.name}' is locked"
    locked, lock = overlap
    if locked is node:
      return f"'{node.name}' is locked by {lock.holder}"
    return f"'{node.name}' overlaps '{locked.name}', locked by {lock.holder}"

  async def release_lock(
    self, request: spb.ReleaseLockRequest, ctx: RequestContext
  ) -> spb.ReleaseLockResponse:
    await self._call(
      lambda: self.engine.release_lock(
        self.engine.resolve(request.resource), request.token, request.force
      )
    )
    condition = self._condition()
    async with condition:
      condition.notify_all()
    return spb.ReleaseLockResponse()

  async def get_lock(self, request: spb.GetLockRequest, ctx: RequestContext) -> spb.GetLockResponse:
    def work() -> spb.GetLockResponse:
      lock = self.engine.lock_holder(self.engine.resolve(request.resource))
      return spb.GetLockResponse(lock=lock) if lock is not None else spb.GetLockResponse()

    return await self._call(work)

  # -- models ------------------------------------------------------------------------------------

  async def put_model(self, request: spb.PutModelRequest, ctx: RequestContext) -> pb.ResourceModel:
    return await self._call(lambda: self.engine.put_model(request.model))

  async def get_model(self, request: spb.GetModelRequest, ctx: RequestContext) -> pb.ResourceModel:
    def work() -> pb.ResourceModel:
      # A copy: a client in this process may change what it is handed.
      model = pb.ResourceModel()
      model.CopyFrom(self.engine.get_model(request.name))
      return model

    return await self._call(work)

  async def list_models(
    self, request: spb.ListModelsRequest, ctx: RequestContext
  ) -> spb.ListModelsResponse:
    def work() -> spb.ListModelsResponse:
      names = self.engine.model_names()
      if request.kind != pb.RESOURCE_KIND_UNSPECIFIED:
        names = [
          name
          for name in names
          if self.engine.get_model(name).definition.attributes.kind == request.kind
        ]
      # Only the page is read: the library's models are read from their files as they are asked for.
      page, token = _page(names, request.page_size, request.page_token)
      # Copied into the response: a client in this process may change what it is handed.
      return spb.ListModelsResponse(
        models=[self.engine.get_model(name) for name in page], next_page_token=token
      )

    return await self._call(work)

  # -- building the tree -------------------------------------------------------------------------

  async def create_resources(
    self, request: spb.CreateResourcesRequest, ctx: RequestContext
  ) -> spb.CreateResourcesResponse:
    def work() -> spb.CreateResourcesResponse:
      created = [self.engine.create(each, request.lock_tokens) for each in request.resources]
      return spb.CreateResourcesResponse(
        nodes=[self.engine.node_message(n) for root in created for n in root.walk()]
      )

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def delete_resources(
    self, request: spb.DeleteResourcesRequest, ctx: RequestContext
  ) -> spb.DeleteResourcesResponse:
    def work() -> spb.DeleteResourcesResponse:
      nodes = [self.engine.resolve(ref) for ref in request.resources]
      for node in nodes:
        if node.id in self.engine.nodes:
          self.engine.delete(node, request.lock_tokens)
      return spb.DeleteResourcesResponse()

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def assign_resources(
    self, request: spb.AssignResourcesRequest, ctx: RequestContext
  ) -> spb.AssignResourcesResponse:
    def work() -> spb.AssignResourcesResponse:
      moved = [self.engine.assign(a, request.lock_tokens, []) for a in request.assignments]
      return spb.AssignResourcesResponse(nodes=self._nodes(moved))

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def unassign_resources(
    self, request: spb.UnassignResourcesRequest, ctx: RequestContext
  ) -> spb.UnassignResourcesResponse:
    def work() -> spb.UnassignResourcesResponse:
      nodes = [self.engine.resolve(ref) for ref in request.resources]
      for node in nodes:
        self.engine.unassign(node, request.lock_tokens, [])
      return spb.UnassignResourcesResponse(nodes=self._nodes(nodes))

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def update_resources(
    self, request: spb.UpdateResourcesRequest, ctx: RequestContext
  ) -> spb.UpdateResourcesResponse:
    def work() -> spb.UpdateResourcesResponse:
      for update in request.updates:
        touched = self.engine.touched_by(update)
        # Turning a resource moves everything under it.
        if update.WhichOneof("update") == "rotation":
          self.engine.check_locks([], request.lock_tokens, moved=touched)
        else:
          self.engine.check_locks(touched, request.lock_tokens)
      issues = [
        issue
        for index, update in enumerate(request.updates)
        if update.WhichOneof("update") == "volume"
        for issue in [
          volume_issue(self.engine.resolve(update.volume.resource), update.volume.volume, index)
        ]
        if issue is not None
      ]
      if issues and request.on_tracking_problem == pb.ON_TRACKING_PROBLEM_REJECT:
        raise ServerError(issues[0].problem, issues[0].message)
      changed: List[Node] = []
      for update in request.updates:
        changed += self.engine.update(update, [])
      return spb.UpdateResourcesResponse(nodes=self._nodes(changed), issues=issues)

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  # -- reading -----------------------------------------------------------------------------------

  def _view(self, view: pb.View) -> Engine:
    return self.engine.committed_view() if view == pb.VIEW_COMMITTED else self.engine

  async def get_resources(
    self, request: spb.GetResourcesRequest, ctx: RequestContext
  ) -> spb.GetResourcesResponse:
    def work() -> spb.GetResourcesResponse:
      engine = self._view(request.view)
      seen: Set[bytes] = set()
      nodes: List[pb.ResourceNode] = []

      def visit(node: Node, depth: int) -> None:
        if node.id not in seen:
          seen.add(node.id)
          nodes.append(engine.node_message(node))
        if depth != 0:
          for child in node.children:
            visit(child, depth - 1)

      asked = [engine.resolve(ref) for ref in request.resources]
      for node in asked:
        visit(node, request.depth)
      if request.ancestors:
        for node in asked:
          parent = node.parent
          while parent is not None:
            visit(parent, 0)
            parent = parent.parent
      return spb.GetResourcesResponse(nodes=nodes, revision=self.engine.revision)

    return await self._call(work)

  async def find_resources(
    self, request: spb.FindResourcesRequest, ctx: RequestContext
  ) -> spb.FindResourcesResponse:
    def work() -> spb.FindResourcesResponse:
      engine = self._view(request.view)
      scopes = (
        [engine.resolve(request.scope)]
        if request.HasField("scope")
        else sorted(engine.roots.values(), key=lambda r: r.name)
      )
      found = [n for scope in scopes for n in scope.walk() if _matches(n, request.filter)]
      page, token = _page(found, request.page_size, request.page_token)
      return spb.FindResourcesResponse(
        nodes=[engine.node_message(n) for n in page],
        next_page_token=token,
        revision=self.engine.revision,
      )

    return await self._call(work)

  async def list_roots(
    self, request: spb.ListRootsRequest, ctx: RequestContext
  ) -> spb.ListRootsResponse:
    def work() -> spb.ListRootsResponse:
      roots = [r for _, r in sorted(self.engine.roots.items()) if _matches(r, request.filter)]
      page, token = _page(roots, request.page_size, request.page_token)
      return spb.ListRootsResponse(
        roots=[self.engine.node_message(r) for r in page],
        next_page_token=token,
        revision=self.engine.revision,
      )

    return await self._call(work)

  async def resolve_geometry(
    self, request: spb.ResolveGeometryRequest, ctx: RequestContext
  ) -> spb.ResolveGeometryResponse:
    def work() -> spb.ResolveGeometryResponse:
      engine = self._view(request.view)
      placements: g.Placements = {}
      return spb.ResolveGeometryResponse(
        results=[engine.geometry(q, placements) for q in request.queries],
        revision=self.engine.revision,
      )

    return await self._call(work)

  async def convert_height_volume(
    self, request: spb.ConvertHeightVolumeRequest, ctx: RequestContext
  ) -> spb.ConvertHeightVolumeResponse:
    def work() -> spb.ConvertHeightVolumeResponse:
      results = []
      for index, query in enumerate(request.queries):
        try:
          container = self.engine.resolve(query.container)
          if query.WhichOneof("given") == "height":
            volume = g.volume_from_height(container, query.height)
            results.append(spb.HeightVolumeResult(height=query.height, volume=volume))
          else:
            height = g.height_from_volume(container, query.volume)
            results.append(spb.HeightVolumeResult(height=height, volume=query.volume))
        except ServerError as refused:
          results.append(
            spb.HeightVolumeResult(
              issue=pb.Issue(problem=refused.problem, message=str(refused), item=index)
            )
          )
      return spb.ConvertHeightVolumeResponse(results=results)

    return await self._call(work)

  async def find_resources_at(
    self, request: spb.FindResourcesAtRequest, ctx: RequestContext
  ) -> spb.FindResourcesAtResponse:
    def work() -> spb.FindResourcesAtResponse:
      engine = self._view(request.view)
      frame = engine.resolve(request.frame)
      exclude = [engine.resolve(ref) for ref in request.exclude]
      found = g.resource_at(frame, to_coordinate(request.location), exclude)
      nodes = []
      while found is not None and found is not frame:
        nodes.append(engine.node_message(found))
        found = found.parent
      return spb.FindResourcesAtResponse(nodes=nodes, revision=self.engine.revision)

    return await self._call(work)

  # -- what a device reports ---------------------------------------------------------------------

  async def record_device_positions(
    self, request: spb.RecordDevicePositionsRequest, ctx: RequestContext
  ) -> spb.RecordDevicePositionsResponse:
    def work() -> spb.RecordDevicePositionsResponse:
      # A part reported somewhere else moves everything under it.
      self.engine.check_locks(
        [], request.lock_tokens, moved=[self.engine.resolve(p.resource) for p in request.positions]
      )
      moved: List[Node] = []
      for position in request.positions:
        node = self.engine.record_position(position, [])
        moved.append(node)
        if position.HasField("jaw_width"):
          moved += list(node.walk())
      return spb.RecordDevicePositionsResponse(nodes=self._nodes(moved))

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def record_measurements(
    self, request: spb.RecordMeasurementsRequest, ctx: RequestContext
  ) -> spb.RecordMeasurementsResponse:
    def work() -> spb.RecordMeasurementsResponse:
      self.engine.check_locks(
        [self.engine.resolve(m.resource) for m in request.measurements], request.lock_tokens
      )
      return spb.RecordMeasurementsResponse(
        results=[
          self.engine.record_measurement(m, i, []) for i, m in enumerate(request.measurements)
        ]
      )

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  # -- commands ----------------------------------------------------------------------------------

  async def begin_tool_pickup(
    self, request: spb.BeginToolPickupRequest, ctx: RequestContext
  ) -> spb.BeginToolPickupResponse:
    response = await self._call(lambda: commands.begin_tool_pickup(self.engine, request))
    response.revision = self.engine.revision
    return response

  async def begin_tool_drop(
    self, request: spb.BeginToolDropRequest, ctx: RequestContext
  ) -> spb.BeginToolDropResponse:
    response = await self._call(lambda: commands.begin_tool_drop(self.engine, request))
    response.revision = self.engine.revision
    return response

  async def begin_liquid_transfer(
    self, request: spb.BeginLiquidTransferRequest, ctx: RequestContext
  ) -> spb.BeginLiquidTransferResponse:
    response = await self._call(lambda: commands.begin_liquid_transfer(self.engine, request))
    response.revision = self.engine.revision
    return response

  async def begin_resource_pickup(
    self, request: spb.BeginResourcePickupRequest, ctx: RequestContext
  ) -> spb.BeginResourcePickupResponse:
    response = await self._call(lambda: commands.begin_resource_pickup(self.engine, request))
    response.revision = self.engine.revision
    return response

  async def begin_resource_place(
    self, request: spb.BeginResourcePlaceRequest, ctx: RequestContext
  ) -> spb.BeginResourcePlaceResponse:
    response = await self._call(lambda: commands.begin_resource_place(self.engine, request))
    response.revision = self.engine.revision
    return response

  # -- operations --------------------------------------------------------------------------------

  async def resolve_operation(
    self, request: spb.ResolveOperationRequest, ctx: RequestContext
  ) -> spb.ResolveOperationResponse:
    def work() -> spb.ResolveOperationResponse:
      op = self.engine.resolve_operation(
        request.operation_id, request.outcomes, request.lock_tokens
      )
      touched = [step.node for outcome in request.outcomes for step in op.items[outcome.item].steps]
      return spb.ResolveOperationResponse(operation=op.message(), nodes=self._nodes(touched))

    response = await self._call(work)
    response.revision = self.engine.revision
    return response

  async def get_operation(
    self, request: spb.GetOperationRequest, ctx: RequestContext
  ) -> pb.Operation:
    def work() -> pb.Operation:
      op = self.engine.operations.get(request.operation_id)
      if op is None:
        raise ServerError(pb.PROBLEM_NOT_FOUND, f"no operation {request.operation_id.hex()}")
      return op.message()

    return await self._call(work)

  async def list_operations(
    self, request: spb.ListOperationsRequest, ctx: RequestContext
  ) -> spb.ListOperationsResponse:
    def work() -> spb.ListOperationsResponse:
      root = self.engine.resolve(request.root) if request.HasField("root") else None
      found = [
        op
        for op in sorted(
          self.engine.operations.values(), key=lambda o: o.created_at.ToNanoseconds()
        )
        if (root is None or op.root is root) and (not request.unresolved_only or op.unresolved())
      ]
      page, token = _page(found, request.page_size, request.page_token)
      return spb.ListOperationsResponse(
        operations=[op.message() for op in page], next_page_token=token
      )

    return await self._call(work)

  # -- watching ----------------------------------------------------------------------------------

  async def watch(
    self, request: spb.WatchRequest, ctx: RequestContext
  ) -> AsyncIterator[spb.ResourceEvent]:
    roots = await self._call(lambda: {self.engine.resolve(ref).root().id for ref in request.roots})
    after = request.after_revision
    sent = 0
    condition = self._condition()
    while True:
      while sent < len(self._events):
        event = self._events[sent]
        sent += 1
        if event.message.revision > after and (not roots or event.roots & roots):
          yield event.message
      async with condition:
        if sent >= len(self._events):
          await condition.wait()

  # -- the primitives ----------------------------------------------------------------------------

  async def apply_changes(
    self, request: ppb.ApplyChangesRequest, ctx: RequestContext
  ) -> ppb.ApplyChangesResponse:
    response = await self._call(lambda: commands.apply_changes(self.engine, request))
    response.revision = self.engine.revision
    return response


def _timestamp() -> Timestamp:
  stamp = Timestamp()
  stamp.GetCurrentTime()
  return stamp


def _matches(node: Node, filter: spb.ResourceFilter) -> bool:
  facts = node.facts
  return (
    (not filter.kinds or facts.kind in filter.kinds)
    and (not filter.models or facts.model in filter.models)
    and (not filter.categories or facts.category in filter.categories)
    and node.name.startswith(filter.name_prefix)
    and (not filter.names or node.name in filter.names)
  )


def _page(items: List[T], size: int, token: str) -> tuple:
  start = int(token) if token else 0
  if size <= 0:
    return items[start:], ""
  end = start + size
  return items[start:end], (str(end) if end < len(items) else "")


def asgi_app(store: MemoryStore) -> Callable[..., Awaitable[None]]:
  """One ASGI application serving both of a store's services."""
  store_app = ResourceServiceASGIApplication(store)
  primitives_app = ResourcePrimitivesServiceASGIApplication(store)
  primitives_prefix = "/pylabrobot.resources.server.v1.ResourcePrimitivesService/"

  async def app(scope: Dict[str, Any], receive: Any, send: Any) -> None:
    if scope.get("type") == "http" and scope.get("path", "").startswith(primitives_prefix):
      await primitives_app(scope, receive, send)
    else:
      await store_app(scope, receive, send)

  return app
