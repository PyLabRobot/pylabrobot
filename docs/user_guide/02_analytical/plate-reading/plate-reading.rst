Plate Readers
=============

PyLabRobot supports the following plate readers:

- `BMG Labtech CLARIOstar`

Plate readers are controlled by the :class:`~pylabrobot.plate_reading.plate_reader.PlateReader` class. This class takes a backend as an argument. The backend is responsible for communicating with the plate reader and is specific to the hardware being used.


.. toctree::
   :maxdepth: 1
   :hidden:

   bmg-clariostar
   cytation5
