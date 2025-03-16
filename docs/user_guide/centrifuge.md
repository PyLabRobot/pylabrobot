# Centrifuges

PyLabRobot supports the following centrifuges:

- {ref}`VSpin <VSpin>`

Centrifuges are controlled by the {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class. This class takes a backend as an argument. The backend is responsible for communicating with the centrifuge and is specific to the hardware being used.

```python
from pylabrobot.centrifuge import Centrifuge
backend = SomeCentrifugeBackend()
pr = Centrifuge(backend=backend)
await pr.setup()
```

The {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.setup` method is used to initialize the centrifuge. This is where the backend will connect to the centrifuge and perform any necessary initialization.

The {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class has a number of methods for controlling the centrifuge. These are:

- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.open_door`: Open the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.close_door`: Close the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_door`: Lock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_door`: Unlock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_bucket`: Lock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_bucket`: Unlock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket1`: Rotate to Bucket 1.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket2`: Rotate to Bucket 2.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.rotate_distance`: Rotate the buckets a specified distance (8000 = 360 degrees).
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.start_spin_cycle`: Start centrifuge spin cycle.

Start spin cycle:

```python
await cf.start_spin_cycle(g = 800, duration = 60)
```

(VSpin)=

## VSpin

The VSpin centrifuge is controlled by the {class}`~pylabrobot.centrifuge.vspin.VSpin` class.

```python
from pylabrobot.centrifuge import Centrifuge, VSpin
cf = Centrifuge(name = 'centrifuge', backend = VSpin(bucket_1_position=0), size_x= 1, size_y=1, size_z=1)
```

### Installation

The VSpin centrifuge connects to your system via a COM port. Integrating it with `pylabrobot` library requires some setup. Follow this guide to get started.

#### 1. Preparing Your Environment

- Windows:

##### Find Your Python Directory

To use the necessary FTDI `.dll` files, you need to locate your Python environment:

1. Open Python in your terminal:
   ```python
   python
   >>> import sys
   >>> sys.executable
   ```
2. This will print a path, e.g., `C:\Python39\python.exe`.
3. Navigate to the `Scripts` folder in the same directory as `python.exe`.

##### **Download FTDI DLLs**

Download the required `.dll` files from the following link:
[FTDI Development Kit](https://sourceforge.net/projects/picusb/files/libftdi1-1.5_devkit_x86_x64_19July2020.zip/download) (link will start download).

1. Extract the downloaded zip file.
2. Locate the `bin64` folder.
3. Copy the files named:
   - `libftdi1.dll`
   - `libusb-1.0.dll`

##### Place DLLs in Python Scripts Folder

Paste the copied `.dll` files into the `Scripts` folder of your Python environment. This enables Python to communicate with FTDI devices.

- macOS:

Install libftdi using [Homebrew](https://brew.sh/):

```bash
brew install libftdi
```

- Linux:

Debian (rpi) / Ubuntu etc:

```bash
sudo apt-get install libftdi-dev
```

Other distros may have similar packages.

#### 2. Configuring the Driver with Zadig

- **This step is only required on Windows.**

Use Zadig to replace the default driver of the VSpin device with `libusbk`:

1. **Identify the VSpin Device**

   - Open Zadig.
   - To confirm the VSpin device, disconnect the RS232 port from the centrifuge while monitoring the Zadig device list.
   - The device that disappears is your VSpin, likely titled "USB Serial Converter."

2. **Replace the Driver**
   - Select the identified VSpin device in Zadig.
   - Replace its driver with `libusbk`.
   - Optionally, rename the device to "VSpin" for easy identification.

> **Note:** If you need to revert to the original driver for tools like the Agilent Centrifuge Config Tool, go to **Device Manager** and uninstall the `libusbk` driver. The default driver will reinstall automatically.

#### 3. Finding the FTDI ID

To interact with the centrifuge programmatically, you need its FTDI device ID. Use the following steps to find it:

1. Open a terminal and run:
   ```bash
   python -m pylibftdi.examples.list_devices
   ```
2. This will output something like:
   ```
   FTDI:USB Serial Converter:FTE0RJ5T
   ```
3. Copy the ID (`FTE0RJ5T` or your equivalent).

#### **4. Setting Up the Centrifuge**

Use the following code to configure the centrifuge in Python:

```python
from pylabrobot.centrifuge import Centrifuge, VSpin

# Replace with your specific FTDI device ID and bucket position for profile in Agilent Centrifuge Config Tool.
backend = VSpin(bucket_1_position=6969, device_id="XXXXXXXX")
centrifuge = Centrifuge(
   backend=backend,
   name="centrifuge",
   size_x=1, size_y=1, size_z=1
)

# Initialize the centrifuge.
await centrifuge.setup()
```

You’re now ready to use your VSpin centrifuge with `pylabrobot`!

### Loader

The VSpin can optionally be used with a loader (called Access2). The loader is optional because you can also use a robotic arm like an iSWAP to move a plate directly into the centrifuge.

Here's how to use the loader:

```python
import asyncio

from pylabrobot.centrifuge import Access2, VSpin
v = VSpin(device_id="FTE1YWTI", bucket_1_position=1314) # bucket 1 position is empirically determined
centrifuge, loader = Access2(name="name", vspin=v, device_id="FTE1YZC5")

# initialize the centrifuge and loader in parallel
await asyncio.gather(
  centrifuge.setup(),
  loader.setup()
)

# go to a bucket and open the door before loading
await centrifuge.go_to_bucket1()
await centrifuge.open_door()

# assign a plate to the loader before loading. This can also be done implicitly by for example
# lh.move_plate(plate, loader)
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb
plate = Cor_96_wellplate_360ul_Fb(name="plate")
loader.assign_child_resource(plate)

# load and unload the plate
await loader.load()
await loader.unload()
```
