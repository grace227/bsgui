# Beamline GUI and Queue Monitoring Toolkit

`bsgui` is a PySide6-based toolkit for beamline data inspection, Bluesky Queue
Server monitoring, and plan preparation. The repository includes a desktop GUI
application, reusable widgets that can be embedded elsewhere, and a Slack bot
for remote queue/scan visibility.

## What the Repository Provides

### Desktop GUI

The GUI entry point is [`main.py`](./main.py). It builds a tabbed application
from registered widgets and YAML configuration files in
[`bsgui/config`](./bsgui/config).

The default application provides two tabs:

- `scan_setup`: a shared data-viewer workspace for microscopy datasets
- `qserver_monitor`: a queue monitor for Bluesky Queue Server

### Data Visualization and Scan Setup

The scan-setup tab combines several pieces from [`bsgui/ui`](./bsgui/ui):

- `XRFLoaderWidget`: browse and load XRF datasets
- `PtychographyLoaderWidget`: browse and load ptychography datasets
- `PlotCanvasWidget`: Matplotlib-backed plotting canvas
- `CustomToolbar`: canvas tools for ROI selection, point selection, and
  `Invert Y`
- `PlanEditorWidget`: build queueable plans and apply ROI/point selections to
  plan parameters
- `QueueServerStatusWidget`: live queue-server status summary
- `QServerConsoleWidget`: live console output pane

Notable viewer functionality:

- shared plotting canvas across loader widgets
- `imshow`-based 2D dataset display
- ROI drawing and removal on the canvas
- point picking on the canvas
- invert-y view toggle in the custom toolbar
- status-bar messages routed through a shared status bus

### Queue Monitoring and Planning

The queue-monitor side of the repository includes:

- `QServerAPI` and `QServerController` in [`bsgui/core`](./bsgui/core) for ZMQ
  communication and polling
- `QueueMonitorWidget` for viewing queue, active item, and history
- queue item normalization helpers in `queue_item_utils.py`
- configurable plan-editor-to-queue integration through ROI key mapping

### Slack Bot

[`bs_slack_bot`](./bs_slack_bot) provides a Socket Mode Slack bot that can:

- report queue status
- report queue history
- report scan progress
- watch console activity and raise stall alerts

Its entry point is [`bs_slack_bot/slackbot.py`](./bs_slack_bot/slackbot.py).

## Repository Layout

```text
bsgui/
  config/        YAML configuration and widget registration defaults
  core/          non-GUI controllers, queue API, parsing helpers
  ui/            PySide6 widgets and plotting components
bs_slack_bot/    Slack bot runtime, handlers, and monitor service
main.py          desktop GUI entry point
requirements.txt dependency list
```

## Installation

Create an environment and install the dependencies listed in
[`requirements.txt`](./requirements.txt):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you use YAML configuration files, also install `PyYAML`:

```bash
pip install PyYAML
```

## Running the GUI

Launch the default application:

```bash
python main.py
```

Run a specific set of tabs:

```bash
python main.py scan_setup qserver_monitor
```

Use a beamline-specific configuration:

```bash
python main.py --beamline s2idd
python main.py --beamline s2ide
python main.py --beamline isn
```

Or provide an explicit config file:

```bash
python main.py --config bsgui/config/widgets.yaml
```

You can also inject additional dataset search paths from the command line:

```bash
python main.py --data-path /path/to/xrf --data-path /path/to/ptycho
```

## Configuration

The GUI is driven by YAML files in [`bsgui/config`](./bsgui/config). These files
control:

- application title and window size
- which tabs are shown
- loader enable/disable flags
- search paths and file patterns for loaders
- plan-editor kinds, parameter overrides, and ROI-to-plan key mapping
- queue-status labels and polling intervals
- console-output options
- grid placement of loader panels, canvas, plan editor, and status widgets

The default examples are:

- [`bsgui/config/widgets.yaml`](./bsgui/config/widgets.yaml)
- [`bsgui/config/s2idd_widgets.yaml`](./bsgui/config/s2idd_widgets.yaml)
- [`bsgui/config/s2ide_widgets.yaml`](./bsgui/config/s2ide_widgets.yaml)
- [`bsgui/config/isn_widgets.yaml`](./bsgui/config/isn_widgets.yaml)

## Queue Server Environment

Live Queue Server features expect these environment variables:

```bash
export QSERVER_ZMQ_CONTROL_ADDRESS=tcp://host:port
export QSERVER_ZMQ_INFO_ADDRESS=tcp://host:port
```

The widget registration code will also look for a local `.env` file if those
variables are not already set.

## Running the Slack Bot

The Slack bot reads settings from environment variables or from a local
`run.sh` file used by the bot settings loader.

Required settings:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
export QSERVER_ZMQ_CONTROL_ADDRESS=tcp://host:port
export QSERVER_ZMQ_INFO_ADDRESS=tcp://host:port
```

Optional settings:

```bash
export SLACK_ALERT_CHANNEL=C12345678
export SLACK_CONSOLE_STALL_SECONDS=1800
export SLACK_CONSOLE_WATCHDOG_POLL_SECONDS=15
```

Start the bot with:

```bash
python bs_slack_bot/slackbot.py
```

## Reuse as a Library

The main reusable exports are available from:

- [`bsgui/ui/__init__.py`](./bsgui/ui/__init__.py) for widget classes
- [`bsgui/core/__init__.py`](./bsgui/core/__init__.py) for controller/data
  helpers
- [`bsgui/widgets.py`](./bsgui/widgets.py) as a compatibility shim for older
  imports

## Security Note

Do not commit Slack tokens, Queue Server credentials, or beamline-specific
secrets into this repository. Keep them in environment variables, a local
untracked `.env`, or another secret-management system.
