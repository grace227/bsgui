# bsgui User Guide

This guide is written as a lightweight in-repo wiki for users who need to run
the GUI, understand its tabs, and know how data and queue state move through
the application.

## 1. What the GUI Is For

`bsgui` combines three practical workflows:

- load and inspect beamline data products
- prepare or submit Bluesky queue plans
- watch the Queue Server and beamline state while scans are running

At runtime, the exact tab set depends on the YAML config you start with. The
default config focuses on `scan_setup` and `qserver_monitor`. The BNP config
adds `beamline_monitor` and `scan_parameter_viewer`.

## 2. Runtime Architecture

```mermaid
flowchart TD
    User[User]
    GUI[bsgui Main Window]
    Tabs[Tab Widgets]
    Controller[QServerController]
    API[QServerAPI]
    QServer[Bluesky Queue Server]
    Worker[Worker Helper Functions]
    Devices[Devices / PVs]

    User --> GUI
    GUI --> Tabs
    Tabs --> Controller
    Controller --> API
    API --> QServer
    QServer --> Worker
    Worker --> Devices
```

The important boundary is:

- `bsgui` renders state and sends user actions
- QServer and worker-side helpers own queue execution and beamline-specific
  monitor logic

## 3. Starting the GUI

Default app:

```bash
python main.py
```

BNP-oriented app:

```bash
python main.py --config bsgui/config/bnp_widgets.yaml
```

Beamline alias if a matching config file exists:

```bash
python main.py --beamline bnp
```

Queue Server variables must be defined for live control and monitoring:

```bash
export QSERVER_ZMQ_CONTROL_ADDRESS=tcp://host:port
export QSERVER_ZMQ_INFO_ADDRESS=tcp://host:port
```

## 4. Main Window Layout

The main window is a `QTabWidget`. Each tab is registered from code and then
instantiated from config.

```mermaid
flowchart LR
    Main[Main Window]
    Main --> ScanSetup[Scan Setup Tab]
    Main --> QueueMonitor[Queue Monitor Tab]
    Main --> BeamlineMonitor[Beamline Monitor Tab]
    Main --> ScanParamViewer[Scan Parameter Viewer Tab]
```

The tabs you actually see depend on the config file.

## 5. Scan Setup Tab

The `scan_setup` tab is the workspace for looking at data and translating a
selection into queue-ready plan parameters.

### Typical layout

```mermaid
flowchart TB
    LoaderPanel[Loader Panel]
    Canvas[Shared Plot Canvas]
    PlanEditor[Plan Editor]
    QueueStatus[Queue Status]
    Console[QServer Console]

    LoaderPanel --> Canvas
    Canvas --> PlanEditor
    QueueStatus --> PlanEditor
    Console --> PlanEditor
```

### What users do here

1. Load an XRF or ptychography dataset.
2. Inspect the plotted image.
3. Draw an ROI or pick a point on the canvas.
4. Let the plan editor map those values into plan parameters.
5. Push the plan to Queue Server.

### Key pieces

- XRF loader for file browsing and beamline data loading
- ptychography loader for scan-number-driven selection
- shared Matplotlib canvas for plotting and ROI work
- plan editor for plan kind selection and argument editing
- queue status panel for quick server state visibility
- console output panel for live QServer messages

## 6. Queue Monitor Tab

The `qserver_monitor` tab is the operational queue view.

### Responsibilities

- inspect pending queue items
- view the currently running plan
- pause, resume, or abort a running scan
- clear or reorder pending entries
- duplicate or delete selected queue rows
- export completed history

### Operational model

```mermaid
sequenceDiagram
    participant User
    participant QueueMonitor
    participant QServerAPI
    participant QServer

    User->>QueueMonitor: click action
    QueueMonitor->>QServerAPI: start/stop/pause/resume request
    QServerAPI->>QServer: ZMQ request
    QServer-->>QServerAPI: response
    QServerAPI-->>QueueMonitor: normalized status
    QueueMonitor-->>User: updated table/progress
```

This tab is where queue control happens. The beamline monitor is primarily a
status and recovery view.

## 7. Beamline Monitor Tab

The `beamline_monitor` tab is intended for plan-aware hardware status.

### What it shows

- current activity text
- snapshot timestamp
- monitor manifest path
- device overview chips
- per-device status rows
- detector recovery controls when a detector advertises recovery support
- a timestamped recovery status box

### Snapshot flow

```mermaid
flowchart LR
    BeamlineMonitorWidget[BeamlineMonitorWidget]
    API[QServerAPI.get_active_plan_monitor_snapshot]
    QServer[QServer helper function]
    Policy[Beamline monitor policy]
    Snapshot[Enriched snapshot]

    BeamlineMonitorWidget --> API
    API --> QServer
    QServer --> Policy
    Policy --> Snapshot
    Snapshot --> BeamlineMonitorWidget
```

The monitor is intentionally moving toward a simpler contract:

- worker side computes `activity`, `summary`, `health`, and `actions`
- GUI side renders those fields with only minimal fallbacks

### Detector recovery flow

```mermaid
sequenceDiagram
    participant User
    participant Widget as BeamlineMonitorWidget
    participant API as QServerAPI
    participant RE as RunEngine / QServer
    participant Helper as worker recover_detector

    User->>Widget: Recover detector
    Widget->>API: recover_detector(name)
    API->>RE: pause
    RE-->>API: paused
    API->>Helper: recover_detector(name)
    Helper-->>API: unhang complete
    API->>RE: resume
    RE-->>API: executing or idle
    API-->>Widget: progress + result
```

The recovery status box in the widget is the user-visible log for this sequence.

## 8. Scan Parameter Viewer Tab

This tab is only present in configurations that enable it. Its purpose is
different from `scan_setup`.

- `scan_setup` is for interactive planning from plotted data
- `scan_parameter_viewer` is for browsing existing Bluesky files and inspecting
  extracted scan metadata

Use it when you need to confirm what arguments were used in prior scans.

## 9. Configuration Model

The GUI is assembled from YAML, not from hardcoded tab lists in `main.py`.

Useful config files:

- `bsgui/config/widgets.yaml`: default app
- `bsgui/config/bnp_widgets.yaml`: BNP-focused app including beamline monitor
- `bsgui/config/s2idd_widgets.yaml`: S2IDD example
- `bsgui/config/s2ide_widgets.yaml`: S2IDE example
- `bsgui/config/isn_widgets.yaml`: ISN example

### What can be configured

- which tabs are enabled
- loader search paths and file patterns
- plan editor kinds and ROI mappings
- queue monitor polling interval and columns
- beamline monitor polling and detector recovery options
- scan-setup grid placement

## 10. Status and Error Reporting

There are two user-facing status paths:

- the application status bar for short operational messages
- in-widget labels for richer state, especially in the beamline monitor

If Queue Server connectivity is missing, many tabs still construct, but live
actions and live polling will not work.

## 11. Screenshots

The repository does not currently ship captured GUI screenshots. To avoid stale
or misleading images, this guide uses diagrams for the current structure and
behavior.

Recommended screenshot set for future additions:

1. Main window with default `scan_setup` and `qserver_monitor` tabs visible
2. `scan_setup` with a loaded XRF map, ROI overlay, and populated plan editor
3. `qserver_monitor` with pending queue items and a running scan
4. `beamline_monitor` showing device status rows and recovery status messages
5. `scan_parameter_viewer` with an opened HDF5 file and extracted parameters

If you add screenshots later, store them under `docs/images/` and link them
from this section.

## 12. Suggested Future Documentation Additions

- a BNP-specific operator guide with detector recovery examples
- one page per beamline config describing required environment and expected tabs
- screenshot-backed walkthroughs for common queue operations
- troubleshooting notes for Queue Server connectivity, missing plans, or worker
  environment state
