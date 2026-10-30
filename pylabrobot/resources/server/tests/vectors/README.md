# Resource store geometry vectors

`geometry.json` holds PLR's answers to geometry questions about whole resource trees: where a
point on a resource is, what it measures once turned, what a height holds, where a parent seats a
child, where a resource goes when placed and where it is gripped, and what is at a point. Every
expected value is computed by PLR's `Resource` classes.

A store in any language checks its geometry against this file. The format is described in
[`../vectors.py`](../vectors.py), which also regenerates it:

```bash
python -m pylabrobot.resources.server.tests.vectors
```
