# PlateHolder

TODO: write a tutorial

See :class:`~pylabrobot.resources.PlateHolder` for the API reference.

## Pedestal z height

> ValueError("pedestal_size_z must be provided. See https://docs.pylabrobot.org/resources/resource-holder/plate-holder.html#pedestal_size_z for more information.")

Many plate carriers feature a "pedestal" or "platform" on the sites. Plates can sit on this pedestal, or directly on the bottom of the site. This depends on the pedestal _and_ plate geometry, so it is important that we know the height of the pedestal.

The pedestal information is not typically available in labware databases (like the VENUS or EVOware databases), and so we rely on users to measure and contribute this information.

For background, see PR 143: [https://github.com/PyLabRobot/pylabrobot/pull/143](https://github.com/PyLabRobot/pylabrobot/pull/143).

### Measuring

Here's how you measure the pedestal height using a ruler or caliper:

![Pedestal height measurement](/resources/img/pedestal/measure.jpeg)

To explain what is happening in the image above: you measure the difference between the pedestal top and the top of the second highest surface: the surface that the plate would sit on if it had a very high clearance below its wells (i.e. a very high `dz` value). It is measured from the top of the pedestal to the top of the second highest surface, so it is a negative value. In some cases, the second highest surface is actually a small ridge, not the bigger outer edge of the plate holder. If that is the case, you measure from the top of the pedestal to the top of that ridge.

#### Measurement with z probing

To measure the height of a surface, you might find [z probing](/user_guide/00_liquid-handling/hamilton-star/z-probing) useful. Z-probing is an automated way, using a pipetting channel, to find the z height of an object. You can see a video of automated measurement on our YouTube channel:

<iframe width="720" height="405" src="https://www.youtube.com/embed/_uPf9hyTBog" title="YouTube video player" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>

Unlike the video, you do not necessarily need to traverse the entire plate, but multiple measurements are recommended to ensure accuracy.

## Contributing

Once you have measured the pedestal height, you can contribute this information to the PyLabRobot Labware database. Here's a guide on contributing to the open-source project: ["How to Open Source"](/contributor_guide/how-to-open-source.md).
