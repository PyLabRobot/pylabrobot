# Devices that need testing

Have access to one of these devices? Run your device's PyLabRobot hello-world example and report
what works and what fails. Reports of successful runs, partial success, and failures are all useful;
you do not need to write a driver or open a pull request to contribute.

## Needs testing

This table lists devices and models flagged as needing hardware testing based on their existing
documentation and driver warnings. **Support** applies to the listed device or model. A shared
driver may support one model while another still needs hardware testing. Models awaiting initial
hardware verification are marked **WIP**.

Use **Show models**, expand a device row, or search for your exact model. Only models that need
testing appear here. Follow **docs** for the guide and known limitations, **code** for the driver,
or **Manager** to find the person looking after it. Some entries still need implementation work;
check their guide before attempting a run.

```{device-table}
:needs-hardware-testing:
```

## Test and report

1. Choose your exact model and read its guide, including setup instructions and known limitations.
   If you need help, the listed manager is available on the
   [forum](https://discuss.pylabrobot.org).
2. Record the model, firmware, connection settings, and PyLabRobot version or Git commit
   (`git rev-parse HEAD` from your checkout). Start with the guide's hello-world example and test
   only the operations appropriate for your hardware and setup.
3. Record each operation you tried, its expected result, and what actually happened on the
   instrument. A command returning without an error is not enough to confirm the physical result.
   Include the script or notebook, output, and relevant I/O logs. Mark steps you did not run as
   **Not tested**; simulation and mock tests do not count as hardware verification.
4. [Submit a device test report](https://github.com/PyLabRobot/pylabrobot/issues/new?template=device-test-report.md).
   Use **Works**, **Partly works**, or **Does not work** in the title and fill in the template.
   Search existing issues first; if there is a report for the same model and problem, add your
   results there. You can also post the same details on the
   [forum](https://discuss.pylabrobot.org) and link any related issue or pull request.

For example, if connection and status queries work but plate retrieval fails, report **Partly
works**, with separate rows for each operation. If setup fails, report **Does not work**, include
the error, and mark later steps as **Not tested**. Successful reports should also include the
code and observed results so someone else can reproduce them.

## What happens to a report

The device manager or a maintainer reviews the evidence and follows up on missing details or
failures. A report applies to the model, firmware, and operations you actually tested; it does
not verify every model in a family.

Once the relevant hardware checks pass, a contributor or maintainer can open a pull request to
set `needs_hardware_testing` to `false` for that device or model in the
{doc}`/contributor_guide/device-registry`, link the report in the device's guide, and update any
not-tested warnings in the guide and driver to match the verified scope. Keep the flag set when
testing remains incomplete or a failure still needs a fix and another hardware run. Update the
tested device or model's support level to reflect its verified capabilities.
