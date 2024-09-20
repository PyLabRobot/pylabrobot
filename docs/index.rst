Welcome to PyLabRobot's documentation!
======================================

PyLabRobot is a hardware agnostic, pure Python SDK for liquid handling robots and accessories.

PyLabRobot provides a layer of general-purpose abstractions over robot functions, with various device drivers for communicating with different kinds of robots. Right now we only support Hamilton STAR and STARLet, Tecan EVO, and Opentrons robots, but we will soon support many more. All of these robots can be controlled using any computer running any operating system. We also provide a browser-based Visualizer which can visualize the state of the deck during a run, and testing backends which do not require access to a robot.

- GitHub repository: https://github.com/PyLabRobot/pylabrobot
- Forum: https://forums.pylabrobot.org
- Paper: https://www.cell.com/device/fulltext/S2666-9986(23)00170-9

.. image:: img/plr.jpg
  :width: 600
  :alt: Graphical abstract of PyLabRobot

.. note::
  PyLabRobot is different from `PyHamilton <https://github.com/dgretton/pyhamilton>`_. While both packages are created by the same lab and both provide a Python interfaces to Hamilton robots, PyLabRobot aims to provide a universal interface to many different robots runnable on many different computers, where PyHamilton is a Windows only interface to Hamilton's VENUS.

.. toctree::
   :maxdepth: 1
   :caption: User Guide

   user_guide/index

.. toctree::
   :maxdepth: 1
   :caption: Development

   contributor_guide/index

.. toctree::
   :maxdepth: 1
   :caption: Resource Library

   resources/index

.. toctree::
   :maxdepth: 2
   :caption: API documentation

   api/pylabrobot



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
