# Sample storage

The HighRes Biosolutions AmbiStore, SteriStore, and TundraStore use the same TCP command protocol
and share a PyLabRobot interface. Choose the concrete class for the model you are configuring; the
product name reported by firmware is retained as version information and does not override that
choice.

```{toctree}
:maxdepth: 1

ambistore/hello-world
steristore/hello-world
tundrastore/hello-world
events
```

## Models

| Model | Environment | Verification |
| --- | --- | --- |
| [AmbiStore](ambistore/hello-world.ipynb) | Ambient storage | Work in progress; not hardware-verified |
| [SteriStore](steristore/hello-world.ipynb) | Heating, active cooling, humidity, CO2, and optional O2 | Hardware-verified |
| [TundraStore](tundrastore/hello-world.ipynb) | Refrigeration and temperature-dependent humidity control | Work in progress; not hardware-verified |

The published temperature ranges and environmental options come from the current
[HighRes sample-storage page](https://www.highres.com/lab-instruments/sample-storage) and an
[archived HighRes sample-storage brochure](https://7157e75ac0509b6a8f5c-5b19c577d01b9ccfe75d2f9e4b17ab55.ssl.cf1.rackcdn.com/RAXZFZSW-PDF-2-613050-4526550137.pdf).

## Network connection

The remote-control server listens on TCP port 1000. The normal factory address is
`192.168.127.60`; HighRes devices also expose the service at `10.253.253.253`. Give the dedicated
host Ethernet interface an address on both subnets so either address remains reachable:

```bash
sudo ip address replace 192.168.127.50/24 dev <interface>
sudo ip address replace 10.253.253.250/24 dev <interface>
```

These `ip address` changes are temporary and disappear when the USB Ethernet adapter is unplugged
or the host restarts. On Linux systems managed by NetworkManager, create a persistent connection
profile instead:

```bash
sudo nmcli connection add \
  type ethernet \
  ifname <interface> \
  con-name highres-sample-storage \
  ipv4.method manual \
  ipv4.addresses "192.168.127.50/24,10.253.253.250/24" \
  ipv4.never-default yes \
  ipv6.method disabled
sudo nmcli connection up highres-sample-storage
```

Use `ip -brief link` to find `<interface>`. Verify the isolated link with
`ping 10.253.253.253`; do not add a gateway or default route to this connection.

## Setup

Pass the storage racks in stacker order and instantiate the appropriate model. For example:

```python
from pylabrobot.high_res.sample_storage import SteriStore

store = SteriStore(host="192.168.127.60", name="steristore", racks=racks)
await store.setup()
```

Each rack maps to its one-based device stacker by list position. Within a rack, the zero-based
carrier `spot` maps to the one-based physical slot: spot 0 is device slot 1, spot 1 is slot 2, and
so on. Dictionary insertion order does not affect this mapping.

During setup, the device-reported transfer nests become `store.nests`. Their locations relative to
the store are left undefined because they depend on the surrounding robot installation. Setup does
not invent plate resources for occupied nests; assign any already-present plates to the matching
nest after setup.

## Plate transfers

Fetch a known plate from its stacker slot to a transfer nest:

```python
plate = await store.fetch_plate_to_loading_tray("plate_1", tray_index=0)
```

Move the plate on a transfer nest back into storage, choosing either a specific `PlateHolder`, the
smallest available site, or a random available site:

```python
await store.take_in_plate(tray_index=0, site="smallest")
```

Both operations update the PLR resource tree only after successful hardware motion. See
[Sample-storage events](events.md) for their structured execution events.

Move a plate directly between two transfer nests using their zero-based tray indices:

```python
plate = await store.transfer_plate_between_nests(
  source_tray_index=1,
  destination_tray_index=0,
)
```

The driver verifies the live source and destination sensors before every fetch, store, or
nest-to-nest transfer. A mismatch between the physical device and the PLR resource tree stops the
operation before motion begins.

## Barcode scans

Barcode scans require every transfer nest to be clear. The driver checks this before starting the
scan because firmware 3.0.0.119 otherwise waits for an automation door and eventually times out.

```python
barcodes = await store.request_stacker_barcodes(2)
barcode = await store.request_stacker_barcodes(2, slot=1)
```

A returned value of `EMPTY` means that the scanner did not read a barcode. It does not prove the
physical slot is empty; use resource bookkeeping or a physical plate-presence workflow for that.

## Recovery

`request_is_parked()` verifies that the device is homed and that both the spatula slide and lift
axes are retracted. `recover()` refuses to move when the spatula sensor reports a plate, because
the plate's physical support must be inspected before a safe recovery path can be chosen.
