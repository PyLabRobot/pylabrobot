# Installation

These instructions describe how to install PyLabRobot.

Note that there are additional installation steps for using the firmware (universal) interface to Hamiltons and Tecans, see {ref}`below <using-the-firmware-interface>`.

## Installing PyLabRobot

It is highly recommended that you install PyLabRobot in a virtual environment. [virtualenv](https://virtualenv.pypa.io/en/latest/) is a popular tool for doing that, but you can use any tool you like. Note that virtualenv needs to be installed separately first.

Here's how to create a virtual environment using virtualenv:

```bash
mkdir your_project
cd your_project
python -m virtualenv env
source env/bin/activate  # on Windows: .\env\Scripts\activate
```

### From source

Alternatively, you can install PyLabRobot from source. This is particularly useful if you want to contribute to the project.

```bash
git clone https://github.com/pylabrobot/pylabrobot.git
cd pylabrobot
pip install -e '.[dev]'
```

See [CONTRIBUTING.md](/contributor_guide/contributing) for specific instructions on testing, documentation and development.

### Using pip (often outdated NOT recommended)

> The PyPI package is often out of date. Please install from source (see above).

The following will install PyLabRobot and the essential dependencies:

```bash
pip install pylabrobot
```

If you want to build documentation or run tests, you need install the additional
dependencies. Also using pip:

```bash
pip install 'pylabrobot[docs]'
pip install 'pylabrobot[testing]'
```

There's a multitude of other optional dependencies that you can install. Replace `[docs]` with one of the following items to install the desired dependencies.

- `fw`: Needed for firmware control over Hamilton robots.
- `http`: Needed for the HTTP backend.
- `websockets`: Needed for the WebSocket backend.
- `simulation`: Needed for the simulation backend.
- `opentrons`: Needed for the Opentrons backend.
- `server`: Needed for LH server, an HTTP front end to LH.
- `agrow`: Needed for the AgrowPumpArray backend.
- `plate_reading`: Needed to interact with the CLARIO Star plate reader.
- `inheco`: Needed for the Inheco backend.
- `dev`: Everything you need for development.
- `all`: Everything. May not be available on all platforms.

To install multiple dependencies, separate them with a comma:

```bash
pip install 'pylabrobot[fw,server]'
```

Or install all dependencies at once:

```bash
pip install 'pylabrobot[all]'
```

(using-the-firmware-interface)=

## Using the firmware interface with Hamilton or Tecan robots

If you want to use the firmware version of the Hamilton or Tecan interfaces, you need to install a backend for [PyUSB](https://github.com/pyusb/pyusb/). You can find the official installation instructions [here](https://github.com/pyusb/pyusb#requirements-and-platform-support). The following is a complete (and probably easier) guide for macOS, Linux and Windows.

Reminder: when you are using the firmware version, make sure to install the firmware dependencies as follows:

```bash
pip install pylabrobot[fw]
```

### On Linux

You should be all set!

### On Mac

You need to install [libusb](https://libusb.info/). You can do this using [Homebrew](https://brew.sh/):

```bash
brew install libusb
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

_These instructions only apply if you are using VENUS on your computer!_

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

## Cytation5 imager

In order to use imaging on the Cytation5, you need to:

1. Install python 3.10
2. Download Spinnaker SDK and install (including Python) [https://www.teledynevisionsolutions.com/products/spinnaker-sdk/](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)
3. Install numpy==1.26 (this is an older version)

If you just want to do plate reading, heating, shaknig, etc. you don't need to follow these specific steps.
