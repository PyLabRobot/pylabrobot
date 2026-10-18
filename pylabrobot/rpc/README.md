# Optional device RPC capabilities

`pylabrobot.rpc` provides identity/status, optional jobs and images, and transport
helpers for device services. Device implementations keep their typed operations,
hardware lifecycle, execution, and persistence. Applications own their device
inventory, connection monitoring, analysis, and user interface.

The protobuf package is `pylabrobot.rpc.v1`. A status-only device needs just
`DeviceService`. Clients select additional capabilities from the fully qualified
service names in `DeviceInfo.services`.

| Capability | Schema | Python adapter/client |
| --- | --- | --- |
| Identity and operational status | [device.proto](v1/device.proto) | `DeviceBackend`, `DeviceService`, `DeviceServer`, `DeviceClient` |
| Optional long-running jobs | [jobs.proto](v1/jobs.proto) | `JobBackend`, `JobService`, `JobClient`, `Job` |
| Optional complete images | [images.proto](v1/images.proto) | `ImageBackend`, `ImageService`, `ImageClient` |

The [Celigo service](../revvity/celigo/grpc/README.md) composes all three with its
own operation service. A pump can compose device/status and jobs without any
image API, codecs, image quota, or Celigo imports. There is no universal command
dictionary or shared hardware executor.

## Installation and connection

RPC requires Python 3.10 or later. Install `PyLabRobot[rpc]` for the common layer;
this does not require NumPy, Pillow, psutil, or the Celigo driver extras. Ordinary
local PLR use does not load the optional RPC modules.

```python
from pathlib import Path

from pylabrobot.rpc.device import DeviceClient
from pylabrobot.rpc.security import Certificates


async def inspect_device() -> None:
  async with DeviceClient(
    "127.0.0.1:50051",
    Certificates(Path("/path/to/client-certificates")),
    device_id="incubator-01",
  ) as client:
    identity = await client.device_info()
    status = await client.status()
    print(identity.display_name, status.state, identity.services)
```

`DeviceClient` verifies device ID and API version. `DeviceServer` binds to
loopback using mutual TLS and registers only `DeviceService`; applications can
register optional generated service adapters on its `server`. The device
application owns backend startup, shutdown, and accepted tasks. Starting the
transport does not initialize hardware.

`Certificates` loads a configured CA and client/server identities. The helper
`create_certificates` generates disposable, seven-day loopback test credentials.
Use an SSH tunnel for remote endpoints. Production provisioning, rotation, and
role permissions are outside this implementation; every trusted client has
operator access.

## Status and multiple devices

Use one stable ID and independent connection per device. Identity advertises its
type, manufacturer/model, implementation and API versions, supported operations,
and registered services. `instance_id` changes on server restart; compare status
revisions only within the same instance.

`observed_at` is the time of the reported operational snapshot, not the time an
RPC read happened. `observation_valid` describes that snapshot's validity and
does not imply recent physical sensor polling. The application tracks last
successful contact and connection state separately. An offline device can retain
its last-known state while the application marks it stale.

Watch streams begin with the current snapshot. `Latest` bounds subscriptions and
keeps one pending snapshot per subscriber, so slow consumers may skip revisions.
Closing a watch releases its subscription without cancelling any device job.
The client application reconnects and reconciles state; neither the registry nor
automatic discovery is implemented in this package.

## Jobs and images

Device-specific typed operations create jobs. The common job API provides
lookup by request ID, start, cancellation, status, and a watch that ends after a
Finished snapshot. State is Ready → Running → Finished, with a separate outcome
and progress unit. A non-imaging operation can report dispenses, steps, or another
unit without promising frames.

The backend must own accepted execution independently of an RPC handler and must
wait for hardware cleanup before reporting cancellation complete. Durable request
deduplication, exclusive execution, persistence, and restart recovery belong to
the backend. `state_durable` reports whether a returned state is persisted; the
shared adapter does not create a database or turn an in-memory task into a durable
job. The Celigo backend supplies these guarantees and marks interrupted execution
on restart without replaying motion.

Image limits and release state belong to `ImageService`, separately from device
identity and `JobStatus`. `GetFrame` returns one complete PNG in a binary field,
with identity, dimensions, timestamp, encoding, and checksum. `ListFrames` pages
metadata only; there are no file handles or application-level image chunks.
`ImageFrame.metadata` carries a typed protobuf `Any` from the device's operation
schema. Generic viewers can display pixels without decoding that metadata;
device clients validate and unpack their own metadata type.

The common message limit defaults to 4 MiB. Imaging implementations configure
larger limits explicitly on both endpoints; Celigo uses 40 MiB messages and a
32 MiB encoded-image limit. Automatic RPC retries are disabled; clients reconcile
ambiguous operation responses by request/job identity.

## Development

```bash
python -m pip install -e '.[rpc-dev]'
python -m pytest -q pylabrobot/rpc/tests
```

The common tests use a fake pump and a status-only incubator with real encrypted
loopback connections. They cover independent devices, optional services,
non-imaging job reconnect/cancellation, identity checks, and watch cleanup. They
also check that common imports do not load Celigo, image libraries, or SQLite.
CI runs them in an environment without Celigo extras and runs the Celigo tests
separately. See the Celigo README for regenerating all dependent bindings.

Preserve published field numbers and reserve removed fields. Service names and
protobuf package names are part of the wire API; changing them requires a
coordinated client/server upgrade or an explicitly supported compatibility layer.
