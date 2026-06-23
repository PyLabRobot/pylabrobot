Agilent BenchCel 4R
===================

The Agilent BenchCel Microplate Handler is an open, sequential stacker system
for moving SBS/ANSI-format plates between vertical stackers and one or more
taught robot-accessible positions. PyLabRobot supports the four-stacker
configuration with
:class:`~pylabrobot.storage.agilent.benchcel_backend.BenchCel4RBackend` and the
:func:`~pylabrobot.storage.agilent.benchcel.BenchCel4R` factory.

.. warning::

   The BenchCel protocol implemented here is reverse-engineered from Agilent
   VWorks packet captures and live tests, not vendor Ethernet documentation.
   Keep the robot/stacker area clear, make sure E-stop/power-off is available,
   and ensure VWorks or any other control client is disconnected before issuing
   motion commands.

Manual safety notes
-------------------

The Agilent BenchCel Microplate Handler R-Series Quick Guide (G5400-90003A)
contains several operational details that matter when automating the device:

* The pendant has a red robot-disable button. Pressing it cuts power to the
  motors and stops motion.
* Compressed air drives the stacker-head mechanisms. Power and compressed air
  must be on for normal operation and rack install/removal workflows.
* The stacker clamps (also called stacker grippers) hold or release the bottom
  plate at the base of a rack. They normally open/close automatically during
  loading, unloading, downstacking, and stacking. Manual open/close is a
  diagnostic/recovery action and can drop plates if the stack is unsupported.
* The stacker shelves temporarily support and level plates during downstacking
  and upstacking. Retracting shelves can drop plates. PyLabRobot does not expose
  a shelf command yet because the captured shelf-related command is not mapped
  with enough confidence.
* The BenchCel is designed for ANSI/SBS-compatible labware. It typically grips
  plates 5-10 mm above the bottom, between the plate top and skirt. Deep lids or
  flexible skirts can cause unreliable gripping or accidental lid removal.

Connection
----------

The BenchCel uses a framed binary protocol over TCP. The default IP address in
our captures was ``192.168.0.100`` and the observed TCP port was ``7612``.

.. code-block:: python

   from pylabrobot.storage.agilent import BenchCel4RBackend

   backend = BenchCel4RBackend(
     host="192.168.0.100",
     port=7612,
     # Optional: bind to the BenchCel-facing network interface on multi-NIC hosts.
     source_ip="192.168.0.200",
   )
   await backend.setup()

Labware profiles and PLR plate dimensions
------------------------------------------

BenchCel/VWorks labware XML uses device-specific dimensions that are not exactly
identical to the dimensions PLR needs:

* ``StackingThickness`` is the vertical pitch between nested plates in a stack.
  It is usually smaller than the full plate height.
* PLR plate ``size_z`` should be the full outside plate height.
* ``RobotGripperOffset`` is the robot gripper contact height from the bottom of
  the plate. In PLR this maps to ``plate.preferred_pickup_location.z`` and to
  ``pickup_distance_from_top = plate.size_z - RobotGripperOffset``.

PyLabRobot calculates BenchCel labware geometry from a PLR plate resource rather
than bundling per-catalog XML profiles. The default calculation is:

* ``StackingThickness = plate.size_z - 1.5 mm``. Override
  ``nesting_overlap`` if a plate family nests differently.
* ``RobotGripperOffset`` is kept between 5 and 8 mm from the bottom while
  preserving at least about 5.4 mm above the grip point where possible.
* ``StackerGripperOffset`` is estimated as 4 mm for low-profile plates, 5 mm for
  standard plates, and 6 mm for tall/deep plates.
* ``SensorOffset`` is estimated as 7 mm for low-profile plates, 8 mm for standard
  plates, and near the top of tall/deep plates.

The supplied example XML/dimension pairs were used to choose the defaults, but
optical thresholds and exact nesting behavior cannot be perfectly inferred from
outside dimensions. Pass explicit overrides when your measured/VWorks values
are known.

.. code-block:: python

   from pylabrobot.resources.plate import Plate
   from pylabrobot.storage.agilent import (
     BenchCel4R,
     apply_benchcel_labware_settings,
     calculate_benchcel_labware_settings,
   )

   plate = Plate("p1", size_x=127.76, size_y=85.48, size_z=10.4, ordered_items={})
   settings = apply_benchcel_labware_settings(plate)
   assert settings.robot_gripper_offset == 5.0
   assert plate.preferred_pickup_location.z == 5.0

   benchcel = BenchCel4R(
     name="benchcel",
     host="192.168.0.100",
     labware=settings,
   )

   # If you know the exact nesting overlap for a plate family, override it.
   settings = calculate_benchcel_labware_settings(plate, nesting_overlap=1.3)

You can also parse user-supplied VWorks XML labware files with
:meth:`~pylabrobot.storage.agilent.benchcel_labware.BenchCelLabwareSettings.from_xml_file`
and supply the full measured/manufacturer plate dimensions. This is useful for
comparing calculated values against the current VWorks settings on the BenchCel
laptop, but the integration does not bundle those XML profiles.

.. note::

   If validation says a PLR plate resource has the wrong height, do not use
   BenchCel stacker motion until the PLR resource and the VWorks labware profile
   agree. For example, ``StackingThickness`` is not an acceptable substitute for
   PLR ``size_z``.

Pushing labware settings to the device
--------------------------------------

VWorks pushes the active labware geometry to the BenchCel over TCP whenever you
apply labware settings. This was confirmed from packet captures: the laptop
sends a 77-byte ``0x7d`` settings frame followed by an empty ``0x9f`` commit that
the device echoes back. Invalid geometry (for example, gripper hold positions
that are not separated) is rejected with a ``0x02`` error such as
``"The labware gripper positions are too close"``.

The backend can push the same settings directly, so you do not need VWorks to
configure the active labware. The payload encoder is byte-for-byte compatible
with the captured VWorks frames for standard flat microplates and includes the
full plate height (offset 37), gripper offsets, sensor thresholds, notch options,
and ``PlatePresenceThreshold`` (offset 75).

.. code-block:: python

   from pylabrobot.resources.plate import Plate
   from pylabrobot.storage.agilent import BenchCel4RBackend

   backend = BenchCel4RBackend(host="192.168.0.100")
   await backend.setup()

   plate = Plate("p1", size_x=127.76, size_y=85.48, size_z=14.4, ordered_items={})
   # Calculates geometry from the plate, encodes 0x7d, sends 0x7d + 0x9f, and
   # raises BenchCelDeviceError if the device rejects the geometry.
   settings = await backend.set_labware(plate)

   # Or push an explicit settings object / serialized dict.
   await backend.set_labware(settings)

.. warning::

   The lidded/sealed sub-fields and ``ErrorCorrectionOffset`` were always zero
   in the captures for standard flat microplates and are not yet mapped, so they
   are sent as zero. Pushing settings for lidded or sealed labware is therefore
   not fully supported yet.

Status and sensors
------------------

VWorks continuously polls each stacker's sensors and a general arm-status frame.
The backend exposes decoded helpers for both.

.. code-block:: python

   sensors = await backend.request_all_stacker_sensors()
   for sensor in sensors:
     print(sensor.stacker, sensor.air_pressure, sensor.plate_presence)

   arm = await backend.request_arm_status()
   print(arm.theta, arm.x, arm.z, arm.gripper)

   bounds = await backend.request_axis_bounds()
   print(bounds.x_min, bounds.x_max)

Stacker and teachpoint moves
----------------------------

Stackers are addressed as human numbers 1 through 4 in the high-level API. The
wire protocol uses zero-based target IDs internally.

.. code-block:: python

   await backend.home()
   await backend.move_to_stacker(3)
   await backend.fully_open_grippers()
   await backend.downstack_plate(3)
   await backend.upstack_plate(4)

VWorks packet captures confirm that ``downstack_plate`` / ``upstack_plate`` are
exactly what the VWorks "Downstack" / "Upstack" buttons emit (a single ``0x62``
/ ``0x63`` robot pick/place at the stacker target), and that ``load_stacker`` /
``unload_stacker`` are the distinct ``0x60`` / ``0x61`` stacker-mechanism commands
behind the "Load" / "Unload" buttons.

If a stacker mechanism is in a bad state, the real device may request an unload
then load recovery cycle, or report errors such as ``Stack not loaded``,
``Stacker shelf position error``, ``Stacker shelf not retracted``, or
``Stacker gripper extended``. The stacker load/unload methods operate the
stacker mechanism, not the robot grippers.

.. code-block:: python

   await backend.unload_stacker(3)
   await backend.load_stacker(3)

The backend also exposes the diagnostic stacker clamp command observed as
``0x67``. Opening the stacker clamps can release/drop a plate stack, so that one
operation keeps a ``dangerously_`` prefix; use it only for recovery/diagnostics
and only when the plate stack is physically supported.

.. code-block:: python

   await backend.dangerously_open_stacker_grippers(1)   # can drop plates
   await backend.close_stacker_grippers(1)

Teachpoints
-----------

BenchCel transfer points are numeric teachpoint slots. The VWorks "right"
teachpoint observed in captures used target ID ``0x1e``. Live tests confirmed
that teachpoints can be written standalone with command ``0x73``; an undefined
teachpoint slot may move the arm to a home-like pose instead of to the desired
location.

.. code-block:: python

   from pylabrobot.storage.agilent import Teachpoint

   await backend.save_teachpoint(
     Teachpoint(
       teachpoint_id=0x1e,
       theta=0.0,
       x=350.0,
       z=0.0,
       approach_height=20.0,
       cavity_depth=0.0,
       gripper_open_limit=-1.5,
       respect_approach_height_when_not_holding_plate=True,
       something_above_this_point=False,
       name="right-transfer",  # metadata only; not sent to the BenchCel
     ),
   )
   await backend.move_to_teachpoint(0x1e, approach_height=20.0)

The BenchCel does not provide a known command to read saved teachpoints back,
and PyLabRobot does not write any files on the host. If you need to keep the
exact numeric teachpoints you wrote, persist them in your own protocol/config.

Using with Stacker resource state
---------------------------------

A BenchCel stacker is a single-ended **LIFO stack of nesting plates**, so PyLabRobot
models the BenchCel with the :class:`~pylabrobot.storage.Stacker` capability (the
sequential/"stacking access" counterpart of the random-access
:class:`~pylabrobot.storage.incubator.Incubator`). The convenience factory returns a
``Stacker`` whose four stacks are :class:`~pylabrobot.resources.resource_stack.ResourceStack`
objects (``direction="z"``); each stack's height follows the plates' ``stacking_z_height``, and
only the **stacker identity** (1-4) is sent to the device. Two transfers move plates between a
stack and the loading tray:

* ``downstack(stack)`` -- move the accessible (top) plate of ``stack`` onto the loading tray.
* ``upstack(stack, plate=None)`` -- move a plate from the loading tray onto ``stack``.

.. important::

   The BenchCel has **no fixed loading/unloading position** -- the transfer
   point is a teachpoint you taught in VWorks (or with ``save_teachpoint``).
   ``downstack`` and ``upstack`` therefore require a teachpoint, either via
   ``loading_tray_teachpoint_id=`` on the factory/backend or ``teachpoint_id=``
   per call. They raise if none is configured, because an unset/wrong teachpoint
   can send the arm to a home-like pose.

   The ``Stacker.loading_tray`` resource and its ``loading_tray_location``
   ``Coordinate`` are **cosmetic** -- they are only used for the PLR resource tree
   and visualization and do not drive any motion. The physical transfer position
   is determined entirely by the teachpoint ID on the device. PLR also models a
   single loading tray, so it cannot represent transfers to several different
   teachpoints distinctly.

.. code-block:: python

   from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
   from pylabrobot.storage.agilent import BenchCel4R, apply_benchcel_labware_settings

   plate = Cor_96_wellplate_360ul_Fb(name="plate_1")
   settings = apply_benchcel_labware_settings(plate)

   benchcel = BenchCel4R(
     name="benchcel",
     host="192.168.0.100",
     loading_tray_teachpoint_id=0x1e,
     labware=settings,
     # Provide your instrument's calibrated deck footprint for visualization.
     size_x=0,
     size_y=0,
     size_z=0,
   )

   benchcel.stacks[2].assign_child_resource(plate)  # PLR state: plate on stacker 3

   await benchcel.setup()
   # Downstack the accessible plate of stacker 3 onto the loading tray:
   fetched = await benchcel.downstack(2, teachpoint_id=0x1e)
   # Upstack it onto stacker 4:
   await benchcel.upstack(3, fetched, teachpoint_id=0x1e)

Mock server
-----------

The implementation includes an in-process mock server for tests and debugging.
It speaks the same framed protocol and can also be run as a small standalone TCP
server.

.. code-block:: bash

   python -m pylabrobot.storage.agilent.benchcel_mock_server --port 7612
