# Celigo gRPC service

Optional device-side service and Python client for the PLR Celigo driver. PLR owns
the protocol, generated bindings, hardware adapter, simulator, and durable jobs
and images. Applications such as workbenches supply their own device registry,
image analysis, records, and user interface.

The canonical protocol is [v1/service.proto](v1/service.proto), with protobuf
package `celigo.v1`. The Python import path is `pylabrobot.revvity.celigo.grpc`.
Neither the server nor the client depends on a workbench package. Importing the
ordinary Celigo driver does not load this optional transport.

`DeviceService`, `JobService`, and `ImageService` are part of `celigo.v1`.
Their names do not imply a device-independent PLR protocol: identity advertises
imaging limits, job retention includes images, and acquisition metadata contains
Celigo stage/galvo fields. Other device types need a separately reviewed common
identity/status contract, optional job and image capabilities, and their own
typed operation services. A dashboard can use per-protocol adapters while that
shared contract is reviewed; device discovery and the web interface live outside
this package.

## Installation

The transport requires Python 3.10 or later. The server uses POSIX file locks
and is tested on Linux. From a PLR checkout containing this package:

```bash
python -m pip install -e '.[celigo-grpc]'
```

Physical operation also needs the `ftdi` extra, the Lumenera SDK, USB access,
and the instrument's vendor calibration/configuration files. These are not
required for the simulator or remote client.

```bash
python -m pip install -e '.[celigo-grpc,ftdi]'
```

## Simulator and connection

Run the hardware-free two-process demonstration:

```bash
celigo-grpc-sim demo
```

It creates temporary certificates and storage, checks whole-image transfers,
concurrent status, cancellation, and reconnect recovery, and removes temporary
data when finished. For a persistent simulator, choose a fresh certificate
directory and a reusable data directory:

```bash
celigo-grpc-sim certificates /path/to/test-certificates
celigo-grpc-sim serve --device-id celigo-sim-01 --port 50051 \
  --certificates /path/to/test-certificates --data-dir /path/to/simulator-data
celigo-grpc-sim check --device-id celigo-sim-01 --endpoint 127.0.0.1:50051 \
  --certificates /path/to/test-certificates
```

Each process serves one device. Run another process with a distinct device ID,
port, and data directory for another instrument. Requests, jobs, and images carry
device identity. Applications can track each device independently.

The listener binds to loopback and requires mutual TLS. Use an SSH tunnel for
remote access. Generated test identities expire after seven days; every trusted
client has operator access. Production certificate provisioning, rotation, and
role permissions remain to be implemented.

## Physical service

```bash
celigo-grpc serve --device-id celigo-01 --port 50052 \
  --certificates /path/to/server-certificates --data-dir /path/to/device-data \
  --config-root /path/to/Celigo --lucam-sdk /path/to/liblucamapi.so \
  --usb-address 1-7 --hardware-lock /path/to/celigo.service.lock
```

Use the actual controller USB topology and calibration for the instrument.
Startup loads configuration and takes an exclusive service lock without opening
the driver or moving hardware. Status starts as Uninitialized. Initialization
requires creating and explicitly starting an initialization job. A restart
requires initialization again; it never automatically repeats hardware work.

`celigo-grpc inspect` reads identity, status, and configuration without motion:

```bash
celigo-grpc inspect --device-id celigo-01 --endpoint 127.0.0.1:50052 \
  --certificates /path/to/client-certificates
```

The physical adapter supports initialization and one brightfield frame at
explicit sample XY, fixed Z, exposure, and gain. It records actual acquisition
metadata. Autofocus, auto-exposure, drawer movement, and full plate scanning are
not exposed by this transport yet. Laser operation is disabled.

## Contract

| Service | Methods |
| --- | --- |
| DeviceService | `GetDevice`, `GetStatus`, `WatchStatus` |
| CeligoService, physical only | `GetConfiguration`, `CreateInitialization`, `CreateBrightfieldCapture` |
| SimulationService, simulator only | `CreateInitialization`, `CreateAcquisition`, `CreatePreview` |
| JobService | `GetJob`, `GetJobByRequestId`, `StartJob`, `CancelJob`, `ReleaseJobImages`, `WatchJob` |
| ImageService | `ListFrames`, `GetFrame`, `WatchPreview` |

Creation is inert and durably deduplicated by request ID. Starting a job commits
acceptance, reserves the instrument, and returns promptly while execution
continues independently of its client. Jobs follow Ready → Running → Finished;
outcome is Pending, Succeeded, Cancelled, Failed, or Interrupted. Cancellation
waits for in-flight hardware work and cleanup before releasing ownership.
Disconnects and RPC deadlines do not cancel accepted execution.

SQLite retains requests, job transitions, and complete images. Restart recovery
marks formerly Running jobs Interrupted, preserves committed images, and leaves
unstarted jobs inert. It never replays motion. Recover a job by request ID after
a lost response. Finished jobs cannot restart. Images are removed only through
explicit `ReleaseJobImages`; request identity and frame metadata remain.

`GetFrame` returns one complete PNG in a protobuf `bytes` field, alongside
identity, timestamp, dimensions, pixel format, checksum, and acquisition metadata.
There are no file handles or application-level image chunks. `ListFrames` pages
metadata only. Re-fetching a retained frame does not capture another image.
The wire contract names physical units explicitly for non-Python clients.

Watch streams send current snapshots and subsequent updates. Slow consumers
may miss intermediate snapshots and must reconcile status and retained frame
metadata. Preview watches deliver whole images and never start acquisition.

Limits include 32 MiB per encoded frame, 40 MiB per gRPC message, 8,388,608 pixels,
and Mono8/Mono16 PNG. Default storage allows 256 MiB of image payload and 1024
retained jobs. Storage quotas reserve space before acceptance; they do not bound
total process memory. The implementation does not guarantee control latency under
saturated RPC load.

## Python client

Use `CeligoClient` for physical devices and `PrototypeClient` for simulators.
Both verify device identity and API version on connection. For example, retrieve
an already completed job's images without starting another acquisition:

```python
from pathlib import Path

from pylabrobot.revvity.celigo.grpc.client import CeligoClient
from pylabrobot.revvity.celigo.grpc.security import Certificates


async def retrieve() -> list[bytes]:
  async with CeligoClient(
    "127.0.0.1:50052",
    Certificates(Path("/path/to/client-certificates")),
    device_id="celigo-01",
  ) as client:
    job = await client.recover("existing-capture-request-id")
    frames = await job.list_frames()
    return [(await job.get_frame(frame.frame_id)).image for frame in frames]
```

## Development

Install the optional development dependencies and run the hardware-free tests
from the PLR repository root:

```bash
python -m pip install -e '.[celigo-grpc-dev]'
python -m pytest pylabrobot/revvity/celigo/grpc/tests
```

The tests use synthetic calibration, fake drivers, encrypted loopback RPCs,
and subprocess crash injection. They require no site inventory or USB devices.
They are skipped during base PLR test collection when transport dependencies
are absent.

Generated bindings and message type stubs ship with PLR. Regenerate all three
outputs with the pinned compiler after editing the schema:

```bash
python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. \
  pylabrobot/revvity/celigo/grpc/v1/service.proto
```

Preserve published field numbers and reserve removed fields. Source-package
relocations must retain `celigo.v1` and its RPC/message contracts.
