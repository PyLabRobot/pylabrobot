# VSpin command-line tools

The server uses POSIX file locks. Install the driver and service dependencies
on the centrifuge's host:

```bash
python -m pip install 'PyLabRobot[ftdi,rpc]'
```

The FTDI driver also requires the system `libftdi` library, as described in
the [VSpin setup notebook](hello-world.ipynb).

Start the service with the centrifuge's FTDI device ID:

```bash
plr dev vspin serve --device-id vspin-01 --ftdi-device-id VSPIN_SERIAL
```

The service listens on `127.0.0.1:50053` and starts Uninitialized. Initialization
and motion require explicit client calls. Read identity, operational status,
and spin limits with:

```bash
plr dev vspin inspect --device-id vspin-01 --endpoint 127.0.0.1:50053
```

`--device-id` is the service's stable identity; `--ftdi-device-id` selects the USB
instrument and its bucket calibration. When omitted, the FTDI driver uses its
device discovery. Job records default to `~/.pylabrobot/vspin/<device-id>`;
`--data-dir` selects another directory and `--max-jobs` controls retained capacity.

`--hardware-lock` defaults to `~/.pylabrobot/vspin/hardware.lock`, independently
of the job directory and service ID. Services targeting the same centrifuge
must use the same lock. Use distinct locks for independent centrifuges.

Use an SSH tunnel for remote connections. The listener uses plaintext gRPC and
does not authenticate callers. `--host` and `--port` change the listen address.

See the [service README](https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/agilent/vspin/grpc/README.md)
for the Python client, job lifecycle, and operation contract. Command help is
available with `plr dev vspin --help` and `plr dev vspin serve --help`.
