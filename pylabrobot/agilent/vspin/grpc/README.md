# VSpin gRPC service

Device service and Python client for the Agilent VSpin centrifuge. Initialization
and spins are durable jobs; door, interlock, and bucket positioning calls wait
for the driver's motion to finish.

The operation protocol is [v1/service.proto](v1/service.proto), with protobuf
package `vspin.v1`. Identity, status, and job lifecycle use the shared
[`pylabrobot.rpc.v1`](../../../rpc/README.md) services.

## Implementation layout

| File | Responsibility |
| --- | --- |
| [session.py](session.py) | `VSpinSession`: driver ownership, device state, and Python operations. |
| [servicer.py](servicer.py) | `VSpinServicer`: request conversion and gRPC error mapping. |
| [server.py](server.py) | `VSpinServer`: service registration and startup/shutdown. |
| [spin_job.py](spin_job.py) | `SpinJob`: execution, cooperative cancellation, and completion. |
| [job_specification.py](job_specification.py) | Validated spin settings and stored representation. |
| [job_store.py](job_store.py) | SQLite records, request deduplication, and restart recovery. |
| [client.py](client.py) | `VSpinClient`: typed operations and shared device/job clients. |
| [remote_job.py](remote_job.py) | `VSpinJob`: client handle and retrieval of retained spin settings. |

## Contract

| Service | Methods |
| --- | --- |
| `pylabrobot.rpc.v1.DeviceService` | `GetDevice`, `GetStatus`, `WatchStatus` |
| `vspin.v1.VSpinService` | `GetConfiguration`, `CreateInitialization`, `CreateSpin`, `GetSpinSettings`, `OpenDoor`, `CloseDoor`, `LockDoor`, `UnlockDoor`, `LockBucket`, `UnlockBucket`, `GoToBucket1`, `GoToBucket2` |
| `pylabrobot.rpc.v1.JobService` | `GetJob`, `GetJobByRequestId`, `StartJob`, `CancelJob`, `WatchJob` |

Starting the server leaves the instrument Uninitialized. Creating a job records
intent without operating hardware. Starting initialization runs `VSpin.setup()`,
including homing. A successful initialization is required before any other motion.
The service retains one driver until shutdown, cancellation of initialization,
or hardware failure.

Spins use the local driver's units: relative centrifugal force `g` in 1–1000,
`duration` in seconds at speed (at least one), and `acceleration`/`deceleration`
as fractions greater than zero and at most one. The driver checks the target
against its controller's encoder range before commanding the spin trajectory.
`GetSpinSettings` returns the exact retained settings, including after restart.

One operation owns the instrument at a time. Concurrent motion is rejected.
Accepted jobs and motion commands continue after RPC deadlines or client
disconnects. Job cancellation requests the driver's controlled deceleration and
remains Running until the spin and cleanup finish. Cancelling initialization
waits for setup to finish and closes the driver. Shutdown follows the same rules.

Request IDs deduplicate identical creation requests. Reusing an ID with different
settings is rejected. Jobs follow Ready → Running → Finished and cannot restart
after completion. SQLite commits acceptance before execution. On service restart,
formerly Running jobs become Interrupted; Ready jobs stay inert. Hardware always
requires explicit initialization after restart. Inspect the instrument after an
interruption before starting another initialization.

Device status reports operational state, active job identity, and faults. Its
timestamp describes the recorded transition, not a live sensor poll. Watch
streams start with current state and coalesce updates for slow subscribers.
Door and bucket commands do not create job records or report physical position;
after a lost response, inspect device status before issuing more motion.
Bucket positioning requires the driver's existing bucket calibration on the
device host. Access2 loader operations and calibration are outside this service.

The store retains up to 1024 jobs by default, including completed requests, so
old request IDs cannot silently cause another spin. `--max-jobs` changes this
limit. Configuration identity includes the selected FTDI connection; retained
jobs cannot start after that connection setting changes. Separate locks protect
the hardware and job directory against competing service processes.

## Python client

```python
from pylabrobot.agilent.vspin.grpc.client import VSpinClient


async def centrifuge() -> None:
  async with VSpinClient("127.0.0.1:50053", device_id="vspin-01") as client:
    initialization = await client.create_initialization("setup-001")
    await initialization.start()
    print(await initialization.wait())

    spin = await client.create_spin("spin-001", g=500, duration=60)
    print(await spin.get_spin_settings())
    await spin.start()
    print(await spin.wait())
```

Use `await spin.cancel()` to request deceleration, then `await spin.wait()` to
wait for completion. After reconnecting, `await client.recover_job("spin-001")`
returns the same job. Check its status to reconcile a lost start response.

See the [CLI guide](../../../../docs/user_guide/agilent/vspin/cli.md) for installation
and service commands.

## Development

```bash
python -m pip install -e '.[rpc-dev]'
make generate-rpc
python -m unittest discover -s pylabrobot/agilent/vspin/grpc/tests -t . -p '*_tests.py'
```

Tests use mocked drivers, loopback RPCs, SQLite, and subprocess crash injection.
Generated bindings and type stubs ship with PLR. Preserve published field
numbers and reserve removed fields; service and protobuf package names are part
of the wire API.
