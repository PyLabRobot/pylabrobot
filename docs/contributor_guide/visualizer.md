# Visualizer Architecture and Contributing

The PyLabRobot visualizer renders the state of a running protocol in a browser.
It is implemented as a lightweight web application served by the
{class}`~pylabrobot.visualizer.visualizer.Visualizer` class in Python.  The
visualizer itself does not perform any simulation logic; instead it receives
messages over a websocket from Python and passively updates the drawing.

This document gives an overview of how the visualizer is structured and where the
code lives so new contributors can quickly get started.

## File layout

The source lives in the :mod:`pylabrobot.visualizer` package:

```
pylabrobot/visualizer/
├── index.html            # entry point served to the browser
├── lib.js                # helper functions and resource definitions
├── vis.js                # websocket setup and event handling
├── main.css              # styling for the page
├── gif.js, gif.worker.js # GIF recording utilities
└── visualizer.py         # Python server component
```

The HTML/JS/CSS files make up a static web page.  The Python
:mod:`visualizer.visualizer` module exposes a :class:`Visualizer` class which
spins up two threads:

1. **File server** – a simple HTTP server that serves the static files above.
2. **Websocket server** – used to push state updates from Python to the browser.

When `Visualizer.setup()` is called, it launches both servers and optionally
opens a browser pointing to the file server.  As resources are assigned to the
root resource or their state changes, callbacks in Python send JSON messages to
the browser via the websocket.

## Browser side

The web page establishes a websocket connection when loaded.  Incoming messages
are dispatched in `vis.js` and update the Konva.js drawing.  The main events are:

- `set_root_resource` – initial resource tree sent when the connection is ready.
- `resource_assigned` / `resource_unassigned` – update the tree structure.
- `set_state` – update tracker state such as volumes or tip usage.

The JavaScript does not compute new state on its own; it simply renders what it
receives.

## Developing the visualizer

To work on the frontend, run a Python script that starts the
:class:`Visualizer` and open the provided URL in your browser.  Changes to the
HTML/JS/CSS files require a reload in the browser.  If you modify the Python
side you may need to restart the script.

Useful entry points are the examples in the user guide or the unit tests in
`pylabrobot/visualizer/visualizer_tests.py` which demonstrate typical usage.

Because communication happens over websockets, you can also drive the visualizer
from unit tests or scripts without a physical robot.  The
{class}`~pylabrobot.liquid_handling.backends.chatterbox.ChatterboxBackend` works
well for this purpose.

## Contributing tips

- Keep the Python server lightweight.  All rendering logic should stay in the
  JavaScript code.
- When sending new events from Python, add matching handlers in `vis.js` and
  document the payload format in comments.
- Try to keep the websocket protocol stable; update the
  `STANDARD_FORM_JSON_VERSION` if breaking changes are introduced.

If you have ideas for improvements or run into issues, feel free to open a topic
on the development forum before submitting a pull request.
