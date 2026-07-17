# Installation

The KX2 arm talks over CAN bus (CANopen / CiA-402); the optional onboard barcode reader is a separate USB-to-serial device. Install the pieces for whichever you use.

## Dependencies

The arm needs the `canopen` extra; the barcode reader needs `serial`:

```bash
pip install "pylabrobot[canopen,serial]"
```

Drop `serial` if you don't have the barcode reader.

## CAN bus (the arm)

The arm connects to the host over its USB-B port, which carries the CAN bus — no separate CAN adapter needed. Just plug the USB-B cable into the host. PLR speaks CANopen (CiA-301 + CiA-402 drive profile) over it via `python-can` (pulled in by the `canopen` extra).

## Barcode reader USB-to-serial driver (optional)

The onboard barcode reader connects via a Prolific PL2303 USB-to-serial cable. Find the port it enumerates on with `python -m serial.tools.list_ports -v`.

### On Linux

Nothing to install — the in-tree `pl2303` kernel module claims the cable automatically and it shows up as `/dev/ttyUSB<n>`.

### On macOS

macOS bundles a driver for older PL2303 variants but not the newer GC/HXN chip (USB ID `067b:23a3`). If the device doesn't appear at `/dev/tty.PL2303G-USBtoUART<n>` after plugging it in, install Prolific's vendor driver:

```bash
brew install --cask prolific-pl2303
open -a PL2303Serial      # registers the system extension
```

Then go to **System Settings → Privacy & Security** and click **Allow** on the "System software from PL2303Serial was blocked" prompt, restart if asked, and replug the cable. Verify with `systemextensionsctl list`: `com.prolific.cdc.PLCdcFSDriver` should show `[activated enabled]`.

### On Windows

```{note}
**TODO.** The PL2303 driver setup on Windows hasn't been written up yet. In general it means installing Prolific's driver so the cable enumerates as a `COM<n>` port, which you then pass as the reader's `port`. If you've done this on Windows, contributions to this section are welcome — see [CONTRIBUTING.md](/contributor_guide/contributing).
```
