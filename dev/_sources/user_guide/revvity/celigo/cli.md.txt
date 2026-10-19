# Celigo CLI

`plr dev celigo` provides two commands for the Celigo gRPC service:

| Command | Purpose |
| --- | --- |
| `serve` | Run the service on the computer connected to the instrument and wait for requests. |
| `inspect` | Connect to a running service, print identity, status, and configuration as JSON, then exit. |

## Installation

The server uses POSIX file locks and is tested on Linux. From a PLR checkout,
install the device imaging and RPC dependencies:

```bash
python -m pip install -e '.[celigo,rpc]'
```

Physical operation also needs the `ftdi` extra, the Lumenera SDK, USB access, and
the instrument's vendor calibration/configuration files. These are not required
for the remote client.

```bash
python -m pip install -e '.[celigo,rpc,ftdi]'
```

View device and command help with:

```bash
plr dev celigo --help
plr dev celigo serve --help
plr dev celigo inspect --help
```

## Run the service

```bash
plr dev celigo serve --config-root /path/to/Celigo
```

Only the instrument's calibration/configuration location is required. The camera
driver uses `LUCAM_SDK_LIBRARY` or system library discovery; set `--lucam-sdk` for
a custom SDK location. Set `--usb-address` when automatic controller discovery
does not identify the instrument.

| Option | Meaning | Default |
| --- | --- | --- |
| `--data-dir` | Directory for durable jobs and images. | `~/.pylabrobot/celigo/<device-id>` |
| `--config-root` | Instrument installation root or configuration directory. | Required |
| `--lucam-sdk` | Path to the Lumenera SDK library. | `LUCAM_SDK_LIBRARY` or system discovery |
| `--hardware-lock` | Path to the exclusive service lock for this instrument. | `~/.pylabrobot/celigo/hardware.lock` |
| `--usb-address` | Controller USB topology, such as `1-7`; supports devices without a serial number. | Automatic discovery |
| `--device-id` | Device identity advertised by the service. | `celigo-01` |
| `--host` | Interface address to listen on; `0.0.0.0` listens on all IPv4 interfaces. | `127.0.0.1` |
| `--port` | Local gRPC listening port. | `50052` |
| `--image-storage-bytes` | Capacity for retained images and reservations across jobs; increase for larger scans. | `10737418240` (10 GiB) |

Startup loads configuration and takes the service lock without opening the
driver or moving hardware. It prints a JSON startup record with the endpoint,
device identity, state, and configuration ID, then keeps running until stopped
with Ctrl+C or SIGTERM.

Status starts as Uninitialized. Initialization requires creating and explicitly
starting an initialization job through the RPC API. A restart requires
initialization again; it never automatically repeats hardware work.

Each process serves one device. The default data directory follows the device ID;
the default lock is shared independently of device ID and data location. For
multiple instruments on one computer, use distinct device IDs, ports, and
`--hardware-lock` paths. Services targeting the same instrument must share a lock
path, including services running as different OS users.

The listener defaults to loopback. To accept connections from other computers:

```bash
plr dev celigo serve --config-root /path/to/Celigo --host 0.0.0.0
```

Supply a specific interface IP instead to bind only that interface. Clients
connect to the server computer's IP or hostname. The listener uses plaintext gRPC
without authentication. An SSH tunnel to the default loopback listener provides
authenticated, encrypted remote access.

## Inspect a running service

```bash
plr dev celigo inspect
```

`inspect` connects to the supplied endpoint, verifies the device identity and API
version, and prints a JSON object with `device`, `status`, and `configuration`
fields. It then exits without initializing the instrument, starting a job, or
accessing USB.

| Option | Meaning | Default |
| --- | --- | --- |
| `--endpoint` | Address of the running service or local SSH tunnel, as `host:port`. | `127.0.0.1:50052` |
| `--device-id` | Expected device identity; must match the service. | `celigo-01` |

## RPC operations

The service supports initialization, drawer opening and closing, individual
brightfield captures, and durable scan jobs. Scans use the same specifications
as the scan-planning notebook: named wells, points, seeded random sampling, and
full coverage, with block grids, multiple channels, and optional image autofocus.
Planning is hardware-free; starting a scan requires initialization and exclusive
instrument ownership. Each image is retained with its plan indices and actual
acquisition metadata and can be retrieved while the scan continues. Auto-exposure
is not exposed, and laser operation is disabled.

See the [Celigo gRPC reference](https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/revvity/celigo/grpc/README.md)
for the protocol, job lifecycle, Python client, and service development details.
