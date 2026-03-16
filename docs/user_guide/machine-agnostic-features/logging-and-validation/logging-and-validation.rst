Logging & Validation
====================

When a protocol runs, you may want to see exactly what happens in real time, review it afterwards, or, in regulated environments, prove it later. Together these form an **audit trail**. The following concepts help you build one:

.. code-block:: text

       Concept              Question it answers          How PLR helps
       =======              =======================      =============

       Audit trail          Who did what, when,          Logging captures every command
                            and on which machine?        and response with timestamps

       Traceability         Can I link this result       IO-level logs record the exact
                            back to what produced it?    sequence of hardware actions

       Data integrity       Is the record complete       Append-mode log files with
                            and unaltered?               timestamps preserve ordering

       Electronic records   Is all of the above          Log files and validation
                            stored digitally?            fixtures are saved to disk

PyLabRobot gives you two tools to support these concepts: **logging** for recording what happens during a run, and **validation** for detecting when protocol behaviour changes between runs. It is up to you to configure them for your environment, and the next pages show you how.

:doc:`Logging <logging>` shows you what is happening *right now*. It prints messages to the console or writes them to a file as your protocol runs - things like which commands are being sent, what the hardware responds, and where errors occur. Use logging when you are developing, debugging, or monitoring a run.

:doc:`Validation <validation>` checks whether a protocol *is going to do the same thing* it did before. It records all communication with the hardware during a known-good run, and replays that recording on future runs to catch any differences. Use validation when you have a working protocol and want to make sure code changes haven't accidentally changed its behaviour.

.. note::

   The initial capture requires a real hardware run - validation cannot work from a simulator alone.

.. note::

   Validation is a good fit for automated testing (CI/CD): run it on every code change to catch unintended differences and protect working protocols, without needing the physical machine.

In short: logging tells you what happened, validation tells you if something changed.

------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   logging
   validation
