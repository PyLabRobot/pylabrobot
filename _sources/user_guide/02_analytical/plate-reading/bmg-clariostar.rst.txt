BMG ClarioSTAR
==============

PyLabRobot supports the following plate readers:

- :ref:`BMG Clariostar <clariostar>`

Plate readers are controlled by the :class:`~pylabrobot.plate_reading.plate_reader.PlateReader` class. This class takes a backend as an argument. The backend is responsible for communicating with the plate reader and is specific to the hardware being used.

.. code-block:: python

   from pylabrobot.plate_reading import PlateReader
   backend = SomePlateReaderBackend()
   pr = PlateReader(backend=backend)
   await pr.setup()

The :meth:`~pylabrobot.plate_reading.plate_reader.PlateReader.setup` method is used to initialize the plate reader. This is where the backend will connect to the plate reader and perform any necessary initialization.

The :class:`~pylabrobot.plate_reading.plate_reader.PlateReader` class has a number of methods for controlling the plate reader. These are:

- :meth:`~pylabrobot.plate_reading.plate_reader.PlateReader.open`: Open the plate reader and make the plate accessible to robotic arms.
- :meth:`~pylabrobot.plate_reading.plate_reader.PlateReader.close`: Close the plate reader and prepare the machine for reading.
- :meth:`~pylabrobot.plate_reading.plate_reader.PlateReader.read_luminescence`: Read luminescence from the plate.
- :meth:`~pylabrobot.plate_reading.plate_reader.PlateReader.read_absorbance`: Read absorbance from the plate.

Read a plate:

.. code-block:: python

   await pr.open()
   move_plate_to_reader()
   await pr.close()
   results = await pr.read_absorbance()

`results` will be a width x height array of absorbance values.

.. _clariostar:

BMG ClarioSTAR
--------------

The BMG CLARIOStar plate reader is controlled by the :class:`~pylabrobot.plate_reading.clario_star_backend.CLARIOStarBackend` class.

.. code-block:: python

   from pylabrobot.plate_reading.clario_star import CLARIOStarBackend
   c = CLARIOStarBackend()


