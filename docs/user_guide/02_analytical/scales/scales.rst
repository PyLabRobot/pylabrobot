Scales
======   

Automated scales are simple but essential devices in laboratory automation. While they 
may seem straightforward, proper integration requires understanding their core capabilities 
and how to interact with them programmatically.

This section focuses on the practical aspects you need to automate weighing operations:

- how to connect to the device (typically serial/USB-serial),
- how to implement the core scale operations,
- how to handle device-specific settings and limitations.

Core Scale Methods
------------------

Every automated scale in PyLabRobot must implement at least three fundamental methods:

``zero()``
~~~~~~~~~~

Calibrates the scale to read zero when nothing is on the weighing platform.
Unlike taring, this doesn't account for any container weight—it simply establishes the 
baseline "empty" reading.
You typically zero a scale at the start of a workflow or when you've removed all items 
from the platform and want to reset to true zero.

``tare()``
~~~~~~~~~~

Resets the scale's reading to zero while accounting for the weight of a container or vessel 
already on the scale.
This is essential when you want to measure only the weight of material being added to a 
container, ignoring the container's own weight.
For example, when dispensing liquid into a beaker, you would first place the empty beaker on 
the scale, tare it, and then measure only the liquid's weight.

``read_weight()``
~~~~~~~~~~~~~~~~~

Retrieves the current weight measurement from the scale.
When you place an item on a scale or add material to a container, the scale doesn't instantly 
settle on a final value—there's a brief period of oscillation as the measurement stabilizes. 
This is due to physical factors like vibrations, air currents, or the mechanical settling of 
the weighing mechanism.

Depending on the scale model and your needs, you may read:

- **Immediate readings**:
  The current value at the moment you query it, which may still be fluctuating.
  For example, if you query the scale while it's still settling, you might get readings like 
  10.23g, 10.25g, 10.24g in quick succession.

- **Stable readings**:
  A value that has been determined to be no longer changing within defined stability criteria.
  Stability can be detected either by the scale's firmware (where the internal electronics monitor 
  consecutive readings and only report when fluctuations fall below a threshold) or by PyLabRobot 
  at a higher software level through repeated polling.

**When to use stable readings**:
In automated workflows where accuracy matters - such as formulation, analytical chemistry, or 
quality control - you should wait for stable readings.
Attempting to use an unstable reading could result in incorrect measurements, failed 
experiments, or out-of-spec products.

**When immediate readings might suffice**:
For monitoring dynamic processes (like continuous dispensing) or when you need rapid feedback 
and can tolerate small variations, immediate readings may be appropriate.


------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   mettler-toledo-WXS205SDU
