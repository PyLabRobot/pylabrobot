Scales
======   

Automated scales are simple but essential devices in laboratory automation. While they 
may seem straightforward, proper integration requires understanding their core capabilities 
and how to interact with them programmatically.

This section focuses on the practical aspects you need to automate weighing operations:

- how to connect to the device (typically serial/USB-serial),
- how to implement the core scale operations,
- how to handle device-specific settings and limitations.

------------------------------------------

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

.. image:: img/scale_0_zero_example.png
   :alt: Zero operation example
   :align: center

``tare()``
~~~~~~~~~~

Resets the scale's reading to zero while accounting for the weight of a container or vessel 
already on the scale.
This is essential when you want to measure only the weight of material being added to a 
container, ignoring the container's own weight.
For example, when dispensing liquid into a beaker, you would first place the empty beaker on 
the scale, tare it, and then measure only the liquid's weight.

.. image:: img/scale_1_tare_example.png
   :alt: Tare operation example
   :align: center

``read_weight()``
~~~~~~~~~~~~~~~~~

Retrieves the current weight measurement from the scale.
When you place an item on a scale or add material to a container, the scale doesn't instantly 
settle on a final value—there's a brief period of oscillation as the measurement stabilizes. 
This is due to physical factors like vibrations, air currents, or the mechanical settling of 
the weighing mechanism.

.. image:: img/scale_2_read_measurement_example.png
   :alt: Read weight operation example
   :align: center

------------------------------------------

Understanding the ``timeout`` Parameter
--------------------------------------------

All three core methods (``zero()``, ``tare()``, and ``read_weight()``) accept a ``timeout`` 
parameter that controls how the scale handles measurement stability.

**Available timeout modes:**

- ``timeout="stable"`` - Wait for a stable reading
  
  The scale will wait indefinitely until the measurement stabilizes. Stability is detected 
  either by the scale's firmware (which monitors consecutive readings internally) or by 
  PyLabRobot polling repeatedly until fluctuations fall below a threshold.
  
  Use this when accuracy is critical: formulation, analytical chemistry, quality control.

- ``timeout=0`` - Read immediately
  
  Returns the current value without waiting, even if still fluctuating. You might get 
  different values like 10.23g, 10.25g, 10.24g in quick succession.
  
  Use this for monitoring dynamic processes or when you need rapid feedback and can 
  tolerate small variations.

- ``timeout=n`` (seconds) - Wait up to n seconds
  
  Attempts to get a stable reading within the specified time. If the reading stabilizes 
  before the timeout, it returns immediately. Otherwise, it returns the current value 
  after n seconds (which may still be unstable).
  
  Use this as a compromise between accuracy and speed, or to prevent indefinite waiting.

**Example usage:**

.. code-block:: python

   await scale.zero(timeout="stable")      # Wait for stability
   await scale.tare(timeout=5)             # Wait max 5 seconds
   weight_g = await scale.read_weight(timeout=0)  # Read immediately


------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   mettler-toledo-WXS205SDU
