# StoreX server

The server owns one StoreX. It owns the serial connection, the cassettes, the
loading tray, and every plate inside that resource tree. A remote workcell owns
none of those objects.

The loading tray is a protocol boundary. Resource ownership crosses it through
a claim, not through a shared Python object.

```text
StoreX rack -> StoreX tray -> claim -> workcell handoff -> workcell destination
   StoreX owns the plate       token        workcell owns the plate
```

`GetState` remains available while a hardware operation is running. Its
`active_operation` field identifies initialization, storage, or retrieval; the
resource snapshot represents the last completed logical state until that
operation finishes.

This is a draft. It implements the storage and handoff path first. Climate,
shaker, door, authentication, durable inventory, and durable operation records
are intentionally outside the first slice.

## Install

Install the serial and server dependencies on the machine connected to the
StoreX:

```bash
pip install 'PyLabRobot[liconic-server]'
```

The `liconic-server` extra composes the serial transport with PyLabRobot's shared
`distributed` extra. The latter owns the compatible `grpcio` and `protobuf`
runtime dependencies used by distributed APIs.

The checked-in Python protobuf modules are generated from `storex.proto`. To
regenerate them while developing, install `grpcio-tools` or use the repository's
`dev` extra, then run:

```bash
python -m grpc_tools.protoc \
  -I. \
  --python_out=. \
  --pyi_out=. \
  --grpc_python_out=. \
  pylabrobot/liconic/storex/experimental_server/storex.proto
python -m ruff check --fix --unsafe-fixes \
  pylabrobot/liconic/storex/experimental_server/storex_pb2*.py
python -m ruff format pylabrobot/liconic/storex/experimental_server/storex_pb2*.py
```

## Run the server

Construct the StoreX exactly as it is physically configured, then give that
object to the server:

```python
import asyncio

from pylabrobot.liconic import StoreX
from pylabrobot.liconic.storex.experimental_server import StoreXServer
from pylabrobot.liconic.storex.racks import storex_rack_17mm_22
from pylabrobot.resources import Coordinate


async def main() -> None:
  storex = StoreX(
    name="storex",
    model="STX44_IC",
    port="/dev/ttyUSB0",
    racks=[storex_rack_17mm_22("cassette_1")],
    loading_tray_location=Coordinate.zero(),
  )
  server = StoreXServer(storex, host="127.0.0.1", port=50051)
  await server.setup()
  try:
    await server.wait_for_termination()
  finally:
    await server.stop()


asyncio.run(main())
```

`server.setup()` connects to the controller. It does not home the handler. Call
`Initialize` only after confirming that the handler and loading tray are clear.

The default listener is loopback-only. Do not bind the draft server to an
untrusted network. It has no authentication or transport security.

## Represent the handoff in a local workcell

The workcell models one local `PlateHolder` at the physical StoreX handoff
coordinate. This holder belongs to the workcell. It is not the StoreX loading
tray and it does not expose the StoreX cassette tree.

```python
from pylabrobot.resources import Coordinate, PlateHolder

storex_handoff = PlateHolder(
  name="storex_handoff",
  size_x=127.76,
  size_y=85.48,
  size_z=0,
  pedestal_size_z=0,
)
deck.assign_child_resource(
  storex_handoff,
  location=Coordinate(950, 120, 145),
)
```

Use the same protobuf contract from a Python workcell client:

```python
import json
import uuid

import grpc

from pylabrobot.liconic.storex.experimental_server import storex_pb2, storex_pb2_grpc
from pylabrobot.resources import Plate, Resource

channel = grpc.aio.insecure_channel("127.0.0.1:50051")
storex = storex_pb2_grpc.StoreXServiceStub(channel)

await storex.FetchPlate(storex_pb2.FetchPlateRequest(plate_name="assay_plate"))

transfer_id = str(uuid.uuid4())
claim = await storex.ClaimTrayPlate(
  storex_pb2.ClaimTrayPlateRequest(transfer_id=transfer_id)
)

resource = Resource.deserialize(json.loads(claim.plate.resource_json))
assert isinstance(resource, Plate)
storex_handoff.assign_child_resource(resource)
```

At this point the StoreX resource tree no longer owns the plate. It retains a
reservation for `transfer_id`, so no other request can use the loading tray.
The workcell owns its deserialized `Plate` and can move it normally:

```python
await liquid_handler.move_resource(resource, to=destination)
await storex.CompleteTransfer(
  storex_pb2.TransferRequest(transfer_id=transfer_id)
)
```

`CompleteTransfer` succeeds only after the StoreX tray sensor is clear.
Repeated calls with the same transfer ID are safe.

If the plate never left the tray, remove the local copy from the workcell model
and release the claim:

```python
resource.unassign()
await storex.ReleaseTransfer(
  storex_pb2.TransferRequest(transfer_id=transfer_id)
)
```

`ReleaseTransfer` succeeds only while the tray sensor still detects the plate.
If it is unclear whether the robot picked the plate, do not guess. Inspect the
workcell and reconcile both models explicitly.

## Put a workcell plate into storage

Place the physical plate on the StoreX tray and send its serialized snapshot.
Keep the local resource until registration succeeds; a retry with the same
snapshot is safe:

```python
plate_json = json.dumps(resource.serialize())

await storex.RegisterTrayPlate(
  storex_pb2.RegisterTrayPlateRequest(resource_json=plate_json)
)
resource.unassign()
await storex.StoreTrayPlate(
  storex_pb2.StoreTrayPlateRequest(
    plate_name=resource.name,
    smallest_fit=storex_pb2.SmallestFit(),
  )
)
```

Registration requires the tray sensor to detect a physical plate. Storage then
moves the server-owned resource from the tray into the StoreX cassette tree.

## Website ownership

A StoreX-only website may fetch a plate to the StoreX tray, but that action does
not modify another process's workcell model. A cross-device button should call a
workcell coordinator. The coordinator performs the fetch, claim, local resource
assignment, robot move, and completion in that order.
