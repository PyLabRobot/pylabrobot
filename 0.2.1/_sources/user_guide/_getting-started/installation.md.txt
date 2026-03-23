# Installation

These instructions describe how to install PyLabRobot.

Note that there are additional installation steps for using the USB interface to Hamiltons and Tecans, see {ref}`below <using-the-usb-interface>`.

## Installing PyLabRobot

It is highly recommended that you install PyLabRobot in a virtual environment rather than using system python.

Here's how to create a virtual environment using `venv`:

```bash
mkdir your_project
cd your_project
python -m venv env
source env/bin/activate  # on Windows: .\env\Scripts\activate
```

### From PyPI

This is the recommended way to install PyLabRobot. It is the easiest and most stable way to get started.

The following will install PyLabRobot:

```bash
pip install pylabrobot
```

If you want to use PLR on physical hardware, you need install the additional dependencies for those. See the example below for how to install the dependencies for the USB interface:

```bash
pip install "pylabrobot[usb]"
```

You can install multiple dependencies at once by separating them with a comma:

```bash
pip install "pylabrobot[serial,usb]"
```

Different machines use different communication modes. Replace `[usb]` with one of the following items to install the desired dependencies. Find specific information for the machines you are using on their respective documentation pages.

| Group | Packages | When you need it |
|-------|----------|-----------------|
| `serial` | pyserial | Serial devices: e.g. BioShake, Cytomat, Inheco (serial mode), Hamilton Tilt Module, Cole Parmer Masterflex, A4S Sealer, XPeel Peeler |
| `usb` | pyusb, libusb-package | USB devices: e.g. Hamilton STAR/STARlet, Tecan EVO (firmware) |
| `ftdi` | pylibftdi, pyusb | FTDI devices: e.g. BioTek Synergy H1 plate reader |
| `hid` | hid | HID devices: e.g. Inheco Incubator/Shaker (HID mode) |
| `modbus` | pymodbus | Modbus devices: e.g. Agrow Pump Array |
| `opentrons` | opentrons-http-api-client | e.g. Opentrons backend |
| `microscopy` | numpy (1.26), opencv-python | e.g. Cytation imager |
| `sila` | zeroconf, grpcio | SiLA devices |
| `pico` | microscopy + sila | ImageXpress Pico microscope |
| `dev` | All of the above + testing/linting tools | Development |

Or install all dependencies:

```bash
pip install 'pylabrobot[all]'
```

Microscopy is not included in the `all` group because it requires an older version of numpy. If you want to use microscopy features, you need to install those dependencies separately through `pip install "pylabrobot[microscopy]"`.

### From source

You can install PyLabRobot from source. This is particularly useful if you want to contribute to the project or if you want to use the latest features that haven't been released on PyPI yet.

```bash
git clone https://github.com/pylabrobot/pylabrobot.git
cd pylabrobot
pip install -e ".[dev]"
```

This will install PyLabRobot in editable mode, which means that any changes you make to the source code will be reflected in your environment without needing to reinstall. It will also install all the dependencies needed for development, testing and building documentation. See above for more information on the different dependency groups.

See [CONTRIBUTING.md](/contributor_guide/contributing) for specific instructions on testing, documentation and development.
(using-the-usb-interface)=

## Using the USB interface

If you want to use the firmware version of the Hamilton or Tecan interfaces, you need to install a backend for [PyUSB](https://github.com/pyusb/pyusb/). You can find the official installation instructions [here](https://github.com/pyusb/pyusb#requirements-and-platform-support). The following is a complete (and probably easier) guide for macOS, Linux and Windows.

First, install the USB dependencies:

```bash
pip install pylabrobot[usb]
```

### On Linux

You should be all set!

### On Mac

You need to install [libusb](https://libusb.info/). You can do this using [Homebrew](https://brew.sh/):

```bash
brew install libusb
```

```{warning}
People have reported issues with not being able to find the machine on macOS 15 Sonoma. No solution to this is currently known. See [this thread](https://labautomation.io/t/usb-device-not-found-error-potential-macos-15-issue/4568).
```

### On Windows

#### Installing

1. Download and install [Zadig](https://zadig.akeo.ie).

2. Make sure the Hamilton is connected using the USB cable and that no other Hamilton/VENUS software is running.

3. Open Zadig and select "Options" -> "List All Devices".

![](/user_guide/img/installation/install-1.png)

4. Select "ML Star" from the list if you're using a Hamilton STAR or STARlet. If you're using a Tecan robot, select "TECU".

![](/user_guide/img/installation/install-2.png)

5. Select "libusbK" using the arrow buttons.

![](/user_guide/img/installation/install-3.png)

6. Click "Replace Driver".

![](/user_guide/img/installation/install-4.png)

7. Click "Close" to finish.

![](/user_guide/img/installation/install-5.png)

#### Uninstalling

_These instructions only apply if you are using VENUS or another vendor software on your computer!_

If you ever wish to switch back from firmware command to use `pyhamilton` or plain VENUS, you have to replace the updated driver with the original Hamilton or Tecan one.

1. This guide is only relevant if ML Star is listed under libusbK USB Devices in the Device Manager program.

![](/user_guide/img/installation/uninstall-1.png)

2. If that"s the case, double click "ML Star" (or similar) to open this dialog, then click "Driver".

![](/user_guide/img/installation/uninstall-2.png)

3. Click "Update Driver".

![](/user_guide/img/installation/uninstall-3.png)

4. Select "Browse my computer for driver software".

![](/user_guide/img/installation/uninstall-4.png)

5. Select "Let me pick from a list of device drivers on my computer".

![](/user_guide/img/installation/uninstall-5.png)

6. Select "Microlab STAR" and click "Next".

![](/user_guide/img/installation/uninstall-6.png)

7. Click "Close" to finish.

![](/user_guide/img/installation/uninstall-7.png)

### Troubleshooting

If you get a `usb.core.NoBackendError: No backend available` error: [this](https://github.com/pyusb/pyusb/blob/master/docs/faq.rst#how-do-i-fix-no-backend-available-errors) may be helpful.

If you are still having trouble, please reach out on [discuss.pylabrobot.org](https://discuss.pylabrobot.org).

## Cytation imager

In order to use imaging on the Cytation, you need to:

1. Install python 3.10
2. Download Spinnaker SDK and install (including Python) [https://www.teledynevisionsolutions.com/products/spinnaker-sdk/](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)
3. Install numpy==1.26 (this is an older version)

If you just want to do plate reading, heating, shaknig, etc. you don't need to follow these specific steps.
