"""How a pod's motion is checked and planned, as pure functions over the pod's model and what it
must clear: no I/O, no `Pod`, no resource tree. The device gives the obstacles, the pod executes
the plans with its own `move_axes`."""
