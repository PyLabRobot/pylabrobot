"""`EventBridge`: a store's changes, followed through its `Watch` stream, as PLR events.

Handles emit `resource.assigned` and `resource.unassigned` for the changes they make, on the
client that makes them. Changes made any other way - raw `store.commands`, staged operations
resolved by a device, another client, another process - reach the bus only from the store. A
bridge follows the store's `Watch` and publishes, for every change in it, the events PLR's
`Resource` gives for the same change, with the same payloads:

  async with EventBridge(store):
    await store.commands.assign_resources(...)  # emits resource.unassigned / resource.assigned

While a bridge follows a store through a client, that client's own resource events are off
(`ResourceServer.client_events`), so a change is published once whichever way it was made. Events
that never touch the store - a device's operations, firmware commands - are emitted on the client
as before.

A bridge's events come from its own task, as the store reports each change, not from inside the
call that made it: `caught_up` waits until every change made so far has been published. They
carry `{"source": "store", "revision": <the store's revision>}` as their context.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Dict, Optional, Sequence, Set

from pylabrobot.events import EventBus, get_event_bus
from pylabrobot.resources.server import handles
from pylabrobot.resources.server.client import ResourceServer
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb


class EventBridge:
  """Publishes a store's resource changes, from its `Watch` stream, to an event bus.

  Args:
    server: The client to watch the store through. Its own resource events are off while the
      bridge runs.
    bus: The bus to publish on; unset, the bus in force where `start` is called, or the default
      bus, at each event.
    roots: Only changes in these trees; empty is every tree.
  """

  def __init__(
    self,
    server: ResourceServer,
    bus: Optional[EventBus] = None,
    roots: Sequence[handles.Resource] = (),
  ):
    self.server = server
    self.bus = bus
    self.roots = list(roots)
    # Every resource the bridge knows, as the last event left it: what the payloads are read from.
    self._nodes: Dict[bytes, pb.ResourceNode] = {}
    self._revision = 0
    self._made_now: Set[bytes] = set()
    self._removed_now: Set[bytes] = set()
    self._task: Optional["asyncio.Task[None]"] = None
    # Notified each time the bridge has applied every event it has been sent.
    self._idle: Optional[asyncio.Condition] = None
    self._waiting = False
    self._received = 0
    self._client_events = server.client_events

  @property
  def revision(self) -> int:
    """The store revision of the last change the bridge has published."""
    return self._revision

  async def start(self) -> None:
    """Read the trees, then follow every change after them; the client's own events go off."""
    if self._task is not None:
      return
    await self._read_trees()
    self._idle = asyncio.Condition()
    self._client_events = self.server.client_events
    self.server.client_events = False
    self._task = asyncio.create_task(self._follow())

  async def stop(self) -> None:
    """Stop following the store; the client's own events are as they were."""
    if self._task is None:
      return
    task, self._task = self._task, None
    if task.done():
      # What it raised went to whoever waited on `caught_up`; taken here, it is not reported.
      if not task.cancelled():
        task.exception()
    else:
      task.cancel()
      # Over Connect, the cancelled stream ends as a refusal.
      with contextlib.suppress(asyncio.CancelledError, ServerError):
        await task
    self.server.client_events = self._client_events

  async def __aenter__(self) -> EventBridge:
    await self.start()
    return self

  async def __aexit__(self, *exc: object) -> None:
    await self.stop()

  async def caught_up(self) -> None:
    """Wait until every change the store has made so far has been published.

    The stream does not mark where a revision's events end, so the bridge is caught up once it has
    seen the store's latest revision, applied everything it was sent, and is sent nothing more
    while it asks the store for its revision again. In this process that is exact: the store sends
    the rest of a revision before it answers another request. Across a network it holds as far
    as the stream keeps pace with requests.

    Raises:
      RuntimeError: The bridge is not running.
    """
    task, idle = self._task, self._idle
    if task is None or idle is None:
      raise RuntimeError("the bridge is not running")
    revision = await self._last_revision()
    while True:
      async with idle:
        await idle.wait_for(lambda: (self._waiting and self._revision >= revision) or task.done())
      if task.done():
        break
      received = self._received
      revision = await self._last_revision()
      if self._waiting and self._received == received and self._revision >= revision:
        break
    if task.done() and not task.cancelled():
      task.result()

  async def _last_revision(self) -> int:
    """The revision of the last change in the trees followed: the store's, when it follows every
    tree; otherwise the latest of any resource in them, since a change in a tree changes one of its
    resources, the parent of one deleted or taken out among them."""
    if not self.roots:
      info = await self.server.commands.get_server_info(spb.GetServerInfoRequest())
      return info.revision
    read = await self.server.commands.get_resources(
      spb.GetResourcesRequest(resources=[root.ref for root in self.roots], depth=-1)
    )
    return max((node.revision for node in read.nodes), default=0)

  # -- following ---------------------------------------------------------------------------------

  async def _read_trees(self) -> None:
    """Every resource in the trees followed, and the revision they were read at."""
    while True:
      if self.roots:
        refs = [root.ref for root in self.roots]
      else:
        refs, token = [], ""
        while True:
          listed = await self.server.commands.list_roots(spb.ListRootsRequest(page_token=token))
          refs.extend(pb.ResourceRef(id=root.id) for root in listed.roots)
          token = listed.next_page_token
          if not token:
            break
      read = await self.server.commands.get_resources(
        spb.GetResourcesRequest(resources=refs, depth=-1)
      )
      info = await self.server.commands.get_server_info(spb.GetServerInfoRequest())
      # A root made between the listing and the reading would be missed: read again.
      if info.revision == read.revision:
        self._nodes = {node.id: node for node in read.nodes}
        self._revision = read.revision
        return

  async def _follow(self) -> None:
    request = spb.WatchRequest(
      roots=[root.ref for root in self.roots], after_revision=self._revision
    )
    idle = self._idle
    assert idle is not None
    events = self.server.commands.watch(request).__aiter__()
    try:
      while True:
        async with idle:
          self._waiting = True
          idle.notify_all()
        event = await events.__anext__()
        self._waiting = False
        self._received += 1
        if event.revision != self._revision:
          self._revision = event.revision
          self._made_now.clear()
          self._removed_now.clear()
        await self._apply(event)
    finally:
      self._waiting = False
      async with idle:
        idle.notify_all()

  async def _apply(self, event: spb.ResourceEvent) -> None:
    which = event.WhichOneof("event")
    if which == "created":
      node = event.created
      self._nodes[node.id] = node
      self._made_now.add(node.id)
      # A tree made at once is assigned at its top: what hangs from it came with it.
      if node.parent_id and node.parent_id not in self._made_now:
        await self._known(node.parent_id)
        self._publish("resource.assigned", handles.assigned_data(self._nodes, node, node.parent_id))
    elif which == "updated":
      node = event.updated
      before = self._nodes.get(node.id)
      self._nodes[node.id] = node
      if spb.CHANGE_ASPECT_PLACEMENT not in event.changed:
        return
      if before is None:
        # It came into a tree followed from one that is not: it and what hangs from it are new.
        read = await self.server.commands.get_resources(
          spb.GetResourcesRequest(resources=[pb.ResourceRef(id=node.id)], depth=-1)
        )
        for each in read.nodes[1:]:
          self._nodes.setdefault(each.id, each)
        if node.parent_id:
          await self._known(node.parent_id)
          self._publish(
            "resource.assigned", handles.assigned_data(self._nodes, node, node.parent_id)
          )
        return
      if before.parent_id == node.parent_id and _location(before) == _location(node):
        return
      if before.parent_id and before.parent_id in self._nodes:
        self._nodes[node.id] = before
        data = handles.unassigned_data(self._nodes, node.id)
        self._nodes[node.id] = node
        if data is not None:
          self._publish("resource.unassigned", data)
      if node.parent_id:
        await self._known(node.parent_id)
        self._publish("resource.assigned", handles.assigned_data(self._nodes, node, node.parent_id))
    elif which == "deleted":
      gone = self._nodes.get(event.deleted)
      self._removed_now.add(event.deleted)
      # A tree removed at once leaves at its top.
      if gone is not None and gone.parent_id and gone.parent_id not in self._removed_now:
        if gone.parent_id in self._nodes:
          data = handles.unassigned_data(self._nodes, gone.id)
          if data is not None:
            self._publish("resource.unassigned", data)
      self._nodes.pop(event.deleted, None)

  async def _known(self, resource_id: bytes) -> None:
    """Read a resource and its ancestors when the bridge does not know them: a tree followed
    took it from one that is not."""
    walking = resource_id
    while walking in self._nodes:
      walking = self._nodes[walking].parent_id
    if not walking:
      return
    read = await self.server.commands.get_resources(
      spb.GetResourcesRequest(resources=[pb.ResourceRef(id=walking)], ancestors=True)
    )
    for node in read.nodes:
      self._nodes.setdefault(node.id, node)

  def _publish(self, name: str, data: Dict[str, Any]) -> None:
    bus = self.bus or get_event_bus()
    if bus is None or not bus.has_listeners:
      return
    bus.emit(name, context={"source": "store", "revision": self._revision}, **data)


def _location(node: pb.ResourceNode) -> Optional[pb.Vec3]:
  return node.attributes.location if node.attributes.HasField("location") else None
