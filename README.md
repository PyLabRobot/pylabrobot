<div style="text-align: center" align="center">
<img width="400" src=".github/img/logo.png" />
</div>

<div style="text-align: center" align="center">
<a href="https://docs.pylabrobot.org"><strong>Docs</strong></a> |
<a href="https://discuss.pylabrobot.org"><strong>Forum</strong></a> |
<a href="https://docs.pylabrobot.org/stable/user_guide/getting-started/installation.html"><strong>Installation</strong></a> |
<a href="https://docs.pylabrobot.org/stable/user_guide/index.html"><strong>Getting started</strong></a> |
  <a href="https://docs.pylabrobot.org/dev/user_guide/machines.html"><strong>Supported Machines</strong></a>
</div>

## What is PyLabRobot?

PyLabRobot is a hardware agnostic, pure Python library for liquid handling robots, plate readers, pumps, scales, heater shakers, and other lab automation equipment. Read [the paper](<https://www.cell.com/device/fulltext/S2666-9986(23)00170-9>) in Device.

Advantages over proprietary software:

- **Cross-platform**: PyLabRobot works on Windows, macOS, and Linux. Many other interfaces are Windows-only.
- **Universal**: PyLabRobot works with any supported liquid handling robot, plate reader, pump, scale, heater shaker, etc. through a single interface.
- **Fast iteration**: PyLabRobot enables rapid development of protocols using atomic commands run interactively in Jupyter notebooks or the Python REPL. This decreases iteration time from minutes to seconds.
- **Open-source**: PyLabRobot is open-source and free to use.
- **Control**: With Python, you have ultimate flexibility to control your lab automation equipment. You can write Turing-complete protocols that include feedback loops.
- **Modern**: PyLabRobot is built on modern Python 3.10+ features and async/await syntax.
- **Fast support**: PyLabRobot has [an active community forum](https://discuss.pylabrobot.org) for support and discussion, and most pull requests are merged within a day.

The [Supported Machines](https://docs.pylabrobot.org/dev/user_guide/machines.html) page lists every machine PyLabRobot supports and its current support level.

## Resources

### Documentation

[docs.pylabrobot.org](https://docs.pylabrobot.org)

- [Installation](https://docs.pylabrobot.org/stable/user_guide/getting-started/installation.html)
- [Getting Started](https://docs.pylabrobot.org/stable/user_guide/index.html)
- [Contributing](https://docs.pylabrobot.org/stable/contributor_guide/index.html)
- [API Reference](https://docs.pylabrobot.org/stable/api/pylabrobot.html)

### Support

- [discuss.pylabrobot.org](https://discuss.pylabrobot.org) for questions and discussions.
- [GitHub Issues](https://github.com/pylabrobot/pylabrobot/issues) for bug reports and feature requests.

## Citing

If you use PyLabRobot in your research, please cite the following:

```bibtex
@article{WIERENGA2023100111,
  title = {PyLabRobot: An open-source, hardware-agnostic interface for liquid-handling robots and accessories},
  journal = {Device},
  volume = {1},
  number = {4},
  pages = {100111},
  year = {2023},
  issn = {2666-9986},
  doi = {https://doi.org/10.1016/j.device.2023.100111},
  url = {https://www.sciencedirect.com/science/article/pii/S2666998623001709},
  author = {Rick P. Wierenga and Stefan M. Golas and Wilson Ho and Connor W. Coley and Kevin M. Esvelt},
  keywords = {laboratory automation, open source, standardization, liquid-handling robots},
}
```

---

**Disclaimer:** PyLabRobot is officially endorsed and supported by an increasing number of robot manufacturers (https://docs.pylabrobot.org/stable/#supported-by), but not all. If you use a firmware driver, such as the STAR driver provided here, you do so at your own risk. Whether it affects your warranty is between you and your vendor. Please contact us with any questions.

_Developed for the Sculpting Evolution Group at the MIT Media Lab_
