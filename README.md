# Beamline Control GUI Components

This repository contains reusable PySide6 widgets for building a beamline control
application. The widgets are decoupled from the main window so they can be
composed into any layout or tabbed interface.

## Available Widgets

- `Beamline Data Viewer` (`scan_setup`): hosts the shared Matplotlib canvas and
  selectively enables the `XRFLoaderWidget`, `PtychographyLoaderWidget`, and the
  new `PlanEditorWidget` so both data modalities and queueable plans can coexist
  in one tab.
- `QServerWidget` (`qserver`): displays the Bluesky Queue Server queue, active
  plan, and recently completed items. It exposes methods (`update_queue`,
  `update_active`, `update_completed`) for integrating with an external QServer
  client.

## Registry and Composition

Widgets are registered via `WidgetDescriptor` definitions stored in a
`WidgetRegistry`. The helper `register_default_widgets` populates the shared
`registry` instance with the widgets above. The example application in
`main.py` consumes this registry to assemble tabs.

```bash
python -m venv .venv
source .venv/bin/activate
pip install PySide6 matplotlib numpy PyYAML
python main.py --data-path /path/to/data
```

Use CLI arguments to choose which widgets to display:

```bash
python main.py scan_setup qserver
```

## YAML Configuration

Customize the layout via `bsgui/config/widgets.yaml`. The example file defines window
metadata, tab order, widget-specific options, and status bar messages. Within
the `scan_setup` tab you can toggle individual loaders under `options.loaders`
(`enabled: false` to disable), set their search paths, and adjust the shared
grid layout. The optional `plan_editor` block lets you enable the
`PlanEditorWidget`, choose which plan kinds to display, and seed static plan
definitions for offline testing.
Launch with the configuration file automatically, or point to a
different one:

```bash
python main.py --config path/to/alternate.yaml
```

## Integrating a Live Bluesky Client

`QServerWidget` accepts an optional `BlueskyQueueClient` implementation that
returns a `QueueSnapshot`. Provide your client when constructing the widget or
subclass it to wire in custom polling, signals, or error handling. The default
registration uses an empty widget instance so applications can inject their own
client at runtime.

Both loader widgets honour layout overrides supplied in the YAML configuration.
Define grid positions (row/column) for the combined loader panel and plot canvas
to switch between a side-by-side or vertically stacked arrangement without
touching the Python code.
