# Cole Parmer Masterflex

PyLabRobot supports the following pumps:

- {ref}`Cole Parmer Masterflex <masterflex>`

## Introduction

Pumps are controlled by the {class}`~pylabrobot.pumps.pump.Pump` class. These take a backend as an argument. The backend is responsible for communicating with the pump and is specific to the hardware being used.

```python
from pylabrobot.pumps import Pump
backend = SomePumpBackend()
p = Pump(backend=backend)
await p.setup()
```

The {meth}`~pylabrobot.pumps.pump.Pump.setup` method is used to initialize the pump. This is where the backend will connect to the pump and perform any necessary initialization.

The {class}`~pylabrobot.pumps.pump.Pump` class has a number of methods for controlling the pump. These are:

- {meth}`~pylabrobot.pumps.pump.Pump.run_continuously`: Run the pump continuously at a given speed.

- {meth}`~pylabrobot.pumps.pump.Pump.run_revolutions`: Run the pump for a given number of revolutions.

- {meth}`~pylabrobot.pumps.pump.Pump.halt`: Stop the pump immediately.

Run the pump for 5 seconds at 100 RPM:

```python
await p.run_continuously(speed=100)
await asyncio.sleep(5)
await p.halt()
```

(masterflex)=

## Cole Parmer Masterflex

The Masterflex pump is controlled by the {class}`~pylabrobot.pumps.cole_parmer.masterflex_backend.MasterflexBackend` class. This takes a serial port as an argument. The serial port is used to communicate with the pump.

```python
from pylabrobot.pumps.cole_parmer.masterflex import MasterflexBackend
m = MasterflexBackend(com_port='/dev/cu.usbmodemDEMO000000001')
```

(I have tried on the L/S 07551-20, but it should work on other models as well.)

Documentation available at: [https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf](https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf)
