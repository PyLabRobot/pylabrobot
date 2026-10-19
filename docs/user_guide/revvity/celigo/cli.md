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
plr dev celigo serve --device-id celigo-01 --port 50052 \
  --data-dir /path/to/device-data \
  --config-root /path/to/Celigo --lucam-sdk /path/to/liblucamapi.so \
  --usb-address 1-7 --hardware-lock /path/to/celigo.service.lock
```

Use the actual controller USB topology and calibration for the instrument.

| Option | Meaning | Default |
| --- | --- | --- |
| `--data-dir` | Directory for durable jobs and images. | Required |
| `--config-root` | Instrument installation root or configuration directory. | Required |
| `--lucam-sdk` | Path to the Lumenera SDK library. | Required |
| `--hardware-lock` | Path to the exclusive service lock for this instrument. | Required |
| `--usb-address` | Controller USB topology, such as `1-7`; supports devices without a serial number. | Automatic discovery |
| `--device-id` | Device identity advertised by the service. | `celigo-01` |
| `--port` | Local gRPC listening port. | `50052` |

Startup loads configuration and takes the service lock without opening the
driver or moving hardware. It prints a JSON startup record with the endpoint,
device identity, state, and configuration ID, then keeps running until stopped
with Ctrl+C or SIGTERM.

Status starts as Uninitialized. Initialization requires creating and explicitly
starting an initialization job through the RPC API. A restart requires
initialization again; it never automatically repeats hardware work.

Each process serves one device. Use a distinct device ID, port, and data directory
for each instrument, with a lock path shared by services targeting the same
instrument.

The listener uses plaintext gRPC bound to loopback. Use an SSH tunnel for remote
access. SSH supplies authentication and encryption for the remote connection;
the RPC listener itself does not authenticate callers.

## Inspect a running service

```bash
plr dev celigo inspect --device-id celigo-01 --endpoint 127.0.0.1:50052
```

`inspect` connects to the supplied endpoint, verifies the device identity and API
version, and prints a JSON object with `device`, `status`, and `configuration`
fields. It then exits without initializing the instrument, starting a job, or
accessing USB.

| Option | Meaning | Default |
| --- | --- | --- |
| `--endpoint` | Address of the running service or local SSH tunnel, as `host:port`. | Required |
| `--device-id` | Expected device identity; must match the service. | `celigo-01` |

## RPC operations

The physical adapter supports initialization, drawer opening and closing, and one
brightfield frame at explicit sample XY, fixed Z, exposure, and gain. Drawer
commands require initialization and exclusive instrument ownership; closing uses
explicit sample XY coordinates. Capture records actual acquisition metadata.
Autofocus, auto-exposure, and full plate scanning are not exposed by this
transport. Laser operation is disabled.

See the [Celigo gRPC reference](https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/revvity/celigo/grpc/README.md)
for the protocol, job lifecycle, Python client, and service development details.
