# Thermocycling

This section provides an overview of how to use thermocyclers in PyLabRobot.

Thermocyclers are essential for temperature-controlled processes like PCR (Polymerase Chain Reaction). PyLabRobot offers a high-level `Thermocycler` class to interact with these devices, along with various backends for different hardware.

## Key Features

- **Temperature Control:** Set and monitor block and lid temperatures.
- **Lid Control:** Open and close the thermocycler lid.
- **Profile Execution:** Run complex temperature profiles, including standard PCR protocols.
- **Status Monitoring:** Query the current state of the thermocycler, including temperature, lid status, and profile progress.

## Supported Thermocyclers

- Opentrons Thermocycler
