# Installation

These instructions describe how to install PyLabRobot.

Note that there are additional installation steps for using the firmware (universal) interface to Hamiltons, see [below](#using-the-firmware-interface-with-hamilton-robots).

## Installing PyLabRobot

It is recommended that you use a virtual environment to install PyLabRobot. See the [virtualenvironment](https://virtualenv.pypa.io/en/latest/) documentation for more information on virtual environments.

Here's how to create a virtual environment in a nutshell:

```bash
mkdir your_project
cd your_project
python -m virtualenv env
source env/bin/activate
```

### Using pip

Installing from PyPI is the easiest and recommended way to install PyLabRobot.

The following will install PyLabRobot and the essential dependencies:

```bash
pip install pylabrobot
```

If you want to build documentation or run tests, you need install the additional
dependencies. Also using pip:

```bash
pip install pylabrobot[docs]
pip install pylabrobot[tests]
```

There's a multitude of other optional dependencies that you can install. Replace `[docs]` with one of the following items to install the desired dependencies.

- `fw`: Needed for firmware control over Hamilton robots.
- `http`: Needed for the HTTP backend.
- `websockets`: Needed for the WebSocket backend.
- `simulation`: Needed for the simulation backend.
- `venus`: Needed for the VENUS backend. This is
  [PyHamilton](https://github.com/dgretton/pyhamilton).
- `server`: Needed for LH server, an HTTP front end to LH.

To install multiple dependencies, separate them with a comma:

```bash
pip install pylabrobot[fw,server]
```

Or install all dependencies at once:

```bash
pip install pylabrobot[all]
```

### From source

Alternatively, you can install PyLabRobot from source. This is particularly useful if you want to contribute to the project.

```bash
git clone https://github.com/pylabrobot/pylabrobot.git
cd pylabrobot
pip install -e '.[all]'
```

See [CONTRIBUTING.md](https://github.com/PyLabRobot/pylabrobot/blob/main/CONTRIBUTING.md) for specific instructions on testing, documentation and development.

## Using the firmware interface with Hamilton robots

If you want to use the firmware version of the Hamilton driver, you need to install a backend for [PyUSB](https://github.com/pyusb/pyusb/). You can find the official installation instructions [here](https://github.com/pyusb/pyusb#requirements-and-platform-support). The following is a complete (and probably easier) guide for macOS, Linux and Windows.

Reminder: when you are using the firmware version, make sure to install the firmware dependencies as follows:

```bash
pip install pylabrobot[fw]
```

### On Linux

You should be all set!

### On Mac

You need to install [libusb](https://libusb.info/). You can do this using [Homebrew](https://brew.sh/):

If you are not working in a conda environment you need to follow [this](https://forums.pylabrobot.org/t/how-to-use-hamilton-star-with-mac/503/4?u=maxhager) instructions.

```bash
brew install libusb
```

### On Windows

#### Installation

1. Download and install [Zadig](https://zadig.akeo.ie).

2. Make sure the Hamilton is connected using the USB cable and that no other software is running.

3. Open Zadig and select "Options" -> "List All Devices".

![](./img/installation/install-1.png)

4. Select "ML Star" from the list.

![](./img/installation/install-2.png)

5. Select "libusbK" using the arrow buttons.

![](./img/installation/install-3.png)

6. Click "Replace Driver".

![](./img/installation/install-4.png)

7. Click "Close" to finish.

![](./img/installation/install-5.png)

#### Uninstallation

_These instructions only apply if you are using VENUS on your computer!_

If you ever wish to switch back from firmware command to use `pyhamilton`, the `VENUS` backend or plain VENUS, you have to replace the updated driver with the original Hamilton one.

1. This guide is only relevant if ML Star is listed under libusbK USB Devices in the Device Manager program.

![](./img/installation/uninstall-1.png)

2. If that"s the case, double click "ML Star" to open this dialog, then click "Driver".

![](./img/installation/uninstall-2.png)

3. Click "Update Driver".

![](./img/installation/uninstall-3.png)

4. Select "Browse my computer for driver software".

![](./img/installation/uninstall-4.png)

5. Select "Let me pick from a list of device drivers on my computer".

![](./img/installation/uninstall-5.png)

6. Select "Microlab STAR" and click "Next".

![](./img/installation/uninstall-6.png)

7. Click "Close" to finish.

![](./img/installation/uninstall-7.png)

### Troubleshooting

If you get a `usb.core.NoBackendError: No backend available` error: [this](https://github.com/pyusb/pyusb/blob/master/docs/faq.rst#how-do-i-fix-no-backend-available-errors) may be helpful.

If you are still having trouble, please reach out on the [forum](https://forums.pylabrobot.org/c/pylabrobot-user-discussion/26).
