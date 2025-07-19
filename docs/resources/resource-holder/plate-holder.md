# PlateHolder

TODO: write a tutorial

See :class:`~pylabrobot.resources.PlateHolder` for the API reference.

## Pedestal z height

> ValueError("pedestal_size_z must be provided. See https://docs.pylabrobot.org/resources/resource-holder/plate-holder.html#pedestal_size_z for more information.")

Many plate carriers feature a "pedestal" or "platform" on the sites. Plates can sit on this pedestal, or directly on the bottom of the site. This depends on the pedestal _and_ plate geometry, so it is important that we know the height of the pedestal.

The pedestal information is not typically available in labware databases (like the VENUS or EVOware databases), and so we rely on users to measure and contribute this information.

Here's how you measure the pedestal height:

![Pedestal height measurement](/resources/img/pedestal/measure.jpeg)

Once you have measured the pedestal height, you can contribute this information to the PyLabRobot Labware database. Here's a guide on contributing to the open-source project: ["How to Open Source"](/contributor_guide/how-to-open-source.md).

For background, see PR 143: [https://github.com/PyLabRobot/pylabrobot/pull/143](https://github.com/PyLabRobot/pylabrobot/pull/143).
