Logging & Validation
====================

Both logging and validation help you understand what your protocol is doing, but they answer different questions.

**Logging** shows you what is happening *right now*. It prints messages to the console or writes them to a file as your protocol runs — things like which commands are being sent, what the hardware responds, and where errors occur. Use logging when you are developing, debugging, or monitoring a run.

**Validation** checks whether a protocol *still does the same thing* it did before. It records all communication with the hardware during a known-good run, and replays that recording on future runs to catch any differences. Use validation when you have a working protocol and want to make sure code changes haven't accidentally changed its behavior. Note: the initial capture requires a real hardware run — validation cannot be bootstrapped from a simulator alone.

In short: logging tells you what happened, validation tells you if something changed.

------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   logging
   validation
