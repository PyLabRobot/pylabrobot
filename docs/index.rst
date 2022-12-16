Welcome to PyLabRobot's documentation!
======================================

PyLabRobot is a hardware agnostic, pure Python library for liquid handling robots.

PyLabRobot provides a layer of general-purpose abstractions over robot functions, with various device drivers for communicating with different kinds of robots. Right now we only have drivers for Hamilton and Opentrons robots, but we will soon have drivers for many more. The two Hamilton drivers are Venus, which is derived from the PyHamilton library, and STAR, which is a low-level firmware interface. We also provide a simulator which plays the role of a device driver but renders commands in a browser-based deck visualization.

.. note::
  PyLabRobot is different from `PyHamilton <https://github.com/dgretton/pyhamilton>`_. While both packages are created by the same lab and both provide a Python interfaces to Hamilton robots, PyLabRobot aims to provide a universal interface to many different robots runnable on many different computers, where PyHamilton is a Windows only interface to Hamilton's VENUS. In service of an easy migration, PyLabRobot is backwards compatible with PyHamilton.

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   installation.md
   contributing.md
   how-to-open-source.md


.. toctree::
   :maxdepth: 1
   :caption: Liquid handling

   basic
   using-the-simulator
   using-trackers
   writing-robot-agnostic-methods


.. toctree::
   :maxdepth: 4
   :caption: API documentation

   pylabrobot


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`


