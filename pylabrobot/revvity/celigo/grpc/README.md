# Celigo gRPC service

Optional device-side service and Python client for the PLR Celigo driver. PLR owns
the protocol, generated bindings, hardware adapter, and durable jobs and images. Applications such as workbenches supply their own device registry,
image analysis, records, and user interface.

The Celigo operation protocol is [v1/service.proto](v1/service.proto), with
protobuf package `celigo.v1`. Shared identity/status, jobs, and images use
[`pylabrobot.rpc.v1`](../../../rpc/README.md). The Python device import path is
`pylabrobot.revvity.celigo.grpc`. Neither server nor client depends on a workbench.
Importing the ordinary Celigo driver does not load this optional transport.

The shared layer supplies contracts and transport adapters. Celigo owns its
acquisition engine, SQLite store, driver lifecycle, and hardware-aware cleanup.
Its image metadata retains typed stage/galvo fields inside the shared envelope's
protobuf `Any`. Other device types can implement identity/status alone or opt into
jobs and images independently, without importing the Celigo implementation.

## Implementation layout

| File | Responsibility |
| --- | --- |
| [session.py](session.py) | `CeligoSession`: driver ownership, instrument state, Python operations, and shared device/job/image backend methods. |
| [servicer.py](servicer.py) | `CeligoServicer`: Celigo RPC requests, response references, and gRPC error mapping. |
| [server.py](server.py) | `CeligoServer`: service registration and startup/shutdown around one session. |
| [acquisition_job.py](acquisition_job.py) | Job execution, cancellation, progress, and completion. |
| [job_specification.py](job_specification.py) | Concrete job settings and their persisted representation. |
| [job_store.py](job_store.py) | SQLite job/image records, recovery, and retention. |
| [image_encoding.py](image_encoding.py) | PNG encoding and image metadata conversion. |
| [limits.py](limits.py) | Capture, image, and message limits. |
| [client.py](client.py) | `CeligoClient`: Python calls to Celigo RPCs, sharing one channel with the device, job, and image clients. |
| [remote_job.py](remote_job.py) | `CeligoImagingJob`: a client handle for starting, watching, cancelling, and retrieving a remote job's images. |

The server passes the same session to the shared device, job, and image services
and the Celigo servicer. The session accepts ordinary Python calls; the Celigo
servicer translates requests and does not own another driver or executor.

## Command-line usage

See the [Celigo CLI guide](../../../../docs/user_guide/revvity/celigo/cli.md) for
installation, connection setup, and the `plr dev celigo serve` and
`plr dev celigo inspect` commands.

## Contract

| Service | Methods |
| --- | --- |
| `pylabrobot.rpc.v1.DeviceService` | `GetDevice`, `GetStatus`, `WatchStatus` |
| `celigo.v1.CeligoService` | `GetConfiguration`, `OpenDoor`, `CloseDoor`, `CreateInitialization`, `CreateBrightfieldCapture` |
| `pylabrobot.rpc.v1.JobService` | `GetJob`, `GetJobByRequestId`, `StartJob`, `CancelJob`, `WatchJob` |
| `pylabrobot.rpc.v1.ImageService` | `GetCapabilities`, `GetJobImageRetention`, `ReleaseJobImages`, `ListFrames`, `GetFrame`, `WatchPreview` |

Creation is inert and durably deduplicated by request ID. Starting a job commits
acceptance, reserves the instrument, and returns promptly while execution
continues independently of its client. Jobs follow Ready → Running → Finished;
outcome is Pending, Succeeded, Cancelled, Failed, or Interrupted. Cancellation
waits for in-flight hardware work and cleanup before releasing ownership.
Disconnects and RPC deadlines do not cancel accepted execution.

`OpenDoor` ejects the drawer; `CloseDoor` loads it at explicit sample-relative XY
coordinates in millimeters. Both require an initialized, idle instrument and wait
for motion to finish. Device status is Busy during drawer motion, with no active
job ID. Concurrent job starts and drawer commands are rejected. Accepted motion
continues if the caller disconnects or its deadline expires, and service shutdown
waits for it. These commands do not create durable job records; after a lost
response, status indicates whether motion is still running or has faulted, but
does not report the drawer's physical position.

SQLite retains requests, job transitions, and complete images. Restart recovery
marks formerly Running jobs Interrupted, preserves committed images, and leaves
unstarted jobs inert. It never replays motion. Recover a job by request ID after
a lost response. Finished jobs cannot restart. Images are removed only through
explicit `ReleaseJobImages`; request identity and frame metadata remain.
Image release state is available through `GetJobImageRetention`, independently
of job status. Image limits and supported encodings come from `GetCapabilities`.

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

`CeligoClient` extends the shared `DeviceClient` and composes `JobClient` and
`ImageClient` on the same channel. Creation and recovery return a `CeligoImagingJob`
remote handle; execution remains in the server's `AcquisitionJob`.

The client verifies device identity, API version, and job/image capabilities on
connection. For example, retrieve an already completed job's images without
starting another acquisition:

```python
from pylabrobot.revvity.celigo.grpc.client import CeligoClient


async def retrieve() -> list[bytes]:
  async with CeligoClient(
    "127.0.0.1:50052",
    device_id="celigo-01",
  ) as client:
    job = await client.recover_job("existing-capture-request-id")
    frames = await job.list_frames()
    return [(await job.get_frame(frame.frame_id)).image for frame in frames]
```

Use `unpack_acquisition_metadata(frame)` from the client module to unpack and validate
Celigo metadata. `job.get_image_retention()` reads release state, and
`job.release_images()` returns that state after durable release.

On an initialized instrument, use `await client.open_door()` to eject the drawer,
and `await client.close_door(sample_x_mm=63.5, sample_y_mm=43.0)` to load it at a
sample-relative position. Supply coordinates appropriate for the carrier; the
server uses the existing driver methods `open_drawer()` and
`close_drawer_to_sample_mm()` without requiring a server-side plate resource.

## Compatibility

Client and server must both use the shared `pylabrobot.rpc.v1` services. The
earlier draft's `celigo.v1.DeviceService`, `JobService`, and `ImageService` RPC
paths are not served as aliases. Upgrade both endpoints together; the existing
device-operation service remains `celigo.v1.CeligoService`.

The SQLite schema remains version 1. Private
[storage.proto](v1/storage.proto) records preserve the original serialized job
and frame field layouts, including image retention and Celigo metadata. Existing
Celigo hardware stores can be read without a data migration. A fixture written
with the earlier schema verifies retained pixels, released images, and
interrupted-job recovery.
Private stored messages are projected into the shared RPC envelopes, so storage
compatibility does not depend on parsing Celigo metadata as a protobuf `Any`.

## Development

Install the optional development dependencies and run the hardware-free tests
from the PLR repository root:

```bash
python -m pip install -e '.[celigo,rpc-dev]'
python -m unittest discover -s pylabrobot/rpc/tests -t . -p '*_tests.py'
python -m unittest discover -s pylabrobot/revvity/celigo/grpc/tests -t . -p '*_tests.py'
```

The tests use `unittest.IsolatedAsyncioTestCase`, synthetic calibration, mocked drivers, loopback RPCs,
and subprocess crash injection. They require no site inventory or USB devices.
They are skipped during base PLR test collection when transport dependencies
are absent.

The standard `.[dev]` extra includes these dependencies. `make typecheck` checks
RPC code and tests against the generated type stubs along with the rest of PLR.

Generated bindings and type stubs ship with PLR. Message stubs come from
`protoc`; service and client stubs come from `mypy-protobuf`. Regenerate all
schemas with the pinned tools from the repository root in your development
environment:

```bash
make generate-rpc
```

Preserve published field numbers and reserve removed fields. Preserve private
storage layouts separately from the public RPC contract.
