# Hettich workflow state and recovery

`centrifuge.state` is an immutable snapshot of the workflow owned by PyLabRobot:

```python
print(centrifuge.state.connection)
print(centrifuge.state.activity)
print(centrifuge.state.recovery_required)
```

`connection` tracks setup and disconnection. It is `unknown` if transport setup or
closure could not be confirmed. Call `stop()` successfully before reconnecting in
that case. `activity` describes the current operation, including hatch motion,
positioning, program selection, and the preparation, acceleration, at-speed, and
braking phases of a spin. `idle` means that no PLR workflow owns the device; it does
not mean that the rotor is physically stopped.

Use `request_status()`, `request_hatch_status()`, and `request_speed()` for live
physical conditions. Status queries remain available while another operation is
active or recovery is required.

## Operation ownership

One workflow owns the device from its prerequisite checks through its final
confirmation. Conflicting motion, setup, recovery, and disconnect calls fail
immediately instead of interleaving their commands.

`stop_spin()` can interrupt a spin owned by PLR. It asks that workflow to stop,
waits for ownership to be released, and verifies standstill, sending the emergency
STOP command if necessary. The interrupted `spin()` call raises an error. A stop
request during preparation prevents START if it has not yet been transmitted.
If no PLR spin is active, `stop_spin()` checks the hardware and stops an active run.

## Recovery after failure

A failed or cancelled operation that may have changed hardware or programmed
settings sets `recovery_required`. The driver records possible changes before
transmitting SELECT, because the device can accept a command whose reply is lost.
For ordinary operations, validation failures before any SELECT do not set this flag.

While recovery is required, ordinary motion and program selection are blocked.
Status reads, emergency stopping, and disconnection remain available. Successful
spin cleanup or disconnecting and reconnecting does not clear the flag.

Resolve the fault and inspect the samples, rotor, hatch, and program settings.
If needed, reconnect with `setup()`, then run:

```python
await centrifuge.recover()
```

Recovery reads and clears the serial fault register, verifies device identity,
and requires error-free remote standstill, zero measured speed, and stable,
fault-free hatch and positioning status. A stationary positioning hold is
allowed if the target position is confirmed. Only successful checks clear the
flag; failed recovery checks leave motion blocked even if no flag was set before
the check. Recovery does not move hardware, restore program settings, or restart an
interrupted cycle.
