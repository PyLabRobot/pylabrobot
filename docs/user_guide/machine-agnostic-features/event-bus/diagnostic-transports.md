# Diagnostic transport events

The EventBus can also report transport and controller diagnostics. These records are distinct from
semantic frontend-operation events, but they inherit enclosing operation context when available.

| Component | Events | Fields |
| --- | --- | --- |
| `io.Serial`, `io.USB`, `io.FTDI` | `io.read`, `io.write` | transport payload details |
| Hamilton USB driver | `firmware.command.started`, `.completed`, `.failed` | issuing module, command, response or error details |
| Brooks PreciseFlex | `precise_flex.firmware_command.started`, `.completed`, `.failed` | controller `device`, command, response or error details |

Use diagnostic records when controller-level or transport-level detail is needed. Semantic events
remain the authoritative description of a public PLR operation.
