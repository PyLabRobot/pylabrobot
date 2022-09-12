# Installation

These instructions describe how to install PyLabRobot.

**If you are using VENUS**, you will probably need to replace the Hamilton driver with a USB driver that is compatible with [PyUSB](https://github.com/pyusb/pyusb). [Later on](#updating-the-usb-driver) in this guide you'll find instructions on how to do this.

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

See [the developer guide](development) for specific instructions on testing, documentation and development.

## Updating the USB driver

_These instructions only apply if you are using VENUS on your computer!_

### Installation

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

### Uninstallation

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
