# RTSP Viewer — Configurable Grid RTSP Client

A PyQt6 + PyAV-based desktop application for viewing and managing up to **sixteen simultaneous RTSP camera feeds**.
The viewer defaults to a fixed-resolution 2×2 grid, and the new Settings panel lets you switch to 1×1, 3×3, or 4×4 layouts with automatic pagination for additional cameras.
Supports per-panel configuration, fullscreen viewing, snapshots, recordings, and persistent config saving/loading.

---

## Features

* **Configurable grid presets** (1×1, 2×2, 3×3, 4×4) with pagination to cover up to 16 feeds.
* **Start/Stop per panel** or **start/stop all at once**.
* **Live RTSP URL preview** as you edit connection settings.
* **TCP/UDP transport selection** with configurable latency.
* **Snapshot capture** to PNG/JPEG.
* **Live recording** to crash-safe MKV format - record any camera feed while viewing.
* **Fullscreen mode** for the active panel.
* **Settings panel** to change grid size, background streaming behavior, and default save destinations.
* **Preset snapshot and recording paths/patterns** with tokens for camera title, index, and timestamp.
* **Save and load configuration** (`.json`), including:

  * All panel connection settings
  * Window geometry
  * Running states (with optional auto-restart on load)
* **Copy panel settings** so you can reuse credentials and URLs across camera slots quickly.
* **Reconnect on error** with automatic retries.
* **Minimal, responsive UI** using PyQt6.

---

## Requirements

This application uses:

* **Python 3.9+**
* [PyQt6](https://pypi.org/project/PyQt6/)
* [PyAV](https://github.com/PyAV-Org/PyAV) (FFmpeg bindings)
* [NumPy](https://numpy.org/)

Make sure FFmpeg is installed on your system — PyAV depends on it:

```bash
# Example for Debian/Ubuntu
sudo apt install ffmpeg libavdevice-dev libavfilter-dev libavformat-dev libavcodec-dev libswscale-dev libavutil-dev
```

---

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Usage

Run the viewer:

```bash
venv/bin/python main.py
```

You’ll see a **2×2 camera panel grid** by default.
Click a panel to make it active (blue border). Use the form fields to configure that panel’s connection.
Use the **Settings** button in the lower-left corner to switch grid presets, manage pagination, or set default save locations.

---

### Connecting a Camera

1. Click a panel to select it.
2. Enter:

   * **User** / **Pass**
   * **IP / Host**
   * **Port** (default: `554`)
   * **Base Path** (e.g., `/cam/realmonitor`)
   * **Channel** and **Subtype**
   * **Transport** (`tcp` or `udp`)
   * **Latency** (ms)
3. Click **Start** to begin streaming.

The **RTSP URL Preview** updates automatically as you edit fields.

Example URL:

```
rtsp://user:pass@192.168.1.10:554/cam/realmonitor?channel=1&subtype=0
```

---

### Controls

| Button                  | Function                                                    |
| ----------------------- | ----------------------------------------------------------- |
| **Start**               | Start the active panel's stream                             |
| **Stop**                | Stop the active panel's stream                              |
| **Snapshot**            | Save current frame from active panel                        |
| **Record**              | Start/stop recording the active panel to MKV file           |
| **Fullscreen (active)** | View active feed fullscreen (Esc/F11/Q to exit)             |
| **Start All**           | Start all configured streams                                |
| **Stop All**            | Stop all streams                                            |
| **Save Config…**        | Save all panel settings + UI state                          |
| **Load Config…**        | Load panel settings + optionally auto-start running streams |
| **Copy to Active**      | Copy credentials, host info, and other fields from the selected panel into the active slot |
| **Previous / Next Page**| Navigate between camera pages when more panes exist         |
| **Settings**            | Open the settings panel (grid presets, background streaming, default paths) |

---

### Settings Panel

The **Settings** dialog appears at the bottom left of the controls column. It exposes:

* **Grid preset** — choose 1×1, 2×2, 3×3, or 4×4 layouts. Larger layouts automatically add pages so you can manage all sixteen feeds.
* **Background streaming** — keep inactive pages running (off by default so streams pause when you leave the page). When enabled, streams only stay active if they were already running; switching pages will not auto-start stopped feeds.
* **Snapshot & recording directories** — define default folders for quick saving.
* **Filename patterns** — generate default filenames using placeholders:
  * `{title}`, `{index}`, `{channel}`, `{timestamp}`, or `$(date)` (alias for the timestamp).

When background streaming is disabled, the viewer pauses streams on hidden pages and resumes them automatically when you return.

---

### Recording

The recording feature allows you to capture the live RTSP stream to a crash-safe MKV file:

1. Start streaming from a camera panel
2. Click the **Record** button to begin recording
3. Choose a location to save the `.mkv` file
4. The button turns red and changes to "Stop Recording" while active
5. Click **Stop Recording** to finish and close the file

**Technical Details:**

* **Format:** Matroska (MKV) container with H.264 video codec
* **Crash-safe:** MKV format allows recovery of recorded video even if the application crashes
* **Parallel recording:** Can record multiple camera feeds simultaneously
* **Quality:** 2 Mbps bitrate, YUV420p color space
* **Frame rate:** Matches the source stream frame rate (typically 30 fps)

The recording runs in parallel with live viewing and will automatically stop when you stop the stream.

---

### Configuration Files

* **config.json** — optional pre-saved configuration.
* **rtsp-url.txt** — example RTSP URLs or notes (not used automatically).
* Saved configs store:

  * Per-panel credentials, host, port, slug, channel, subtype, transport, latency, and running state.
  * Active panel index and window size.
  * Fullscreen visibility.
  * Grid preset, background streaming preference, and default snapshot/recording save patterns.

---

## Directory Structure

```
rtsp-client/
├── config.json          # Optional saved configuration
├── LICENSE              # License file
├── README.md            # This file
├── requirements.txt     # Dependencies list
├── rtsp-url.txt         # Example RTSP URL(s) or notes
├── main.py              # Main application (single-file)
└── venv/                # (Optional) Python virtual environment
```

---

## Keyboard & Mouse Shortcuts

* **Click panel** — select active panel.
* **Esc / F11 / Q** — exit fullscreen.

---

## Changelog

### Unreleased

* Added a settings panel with grid presets (1×1 through 4×4), pagination controls, and a background streaming toggle.
* Added configurable snapshot and recording save directories with pattern-based default filenames.
* Extended the viewer to manage up to sixteen camera feeds through multi-page navigation.
* Added a per-panel copy helper and refined background streaming so switching pages never auto-starts inactive feeds.

---

## License

This project is licensed under the terms of the [LICENSE](LICENSE) file.

---

## Notes & Tips

* **Fixed-size scaling** ensures feeds don’t grow as new frames arrive.
* If your feed fails to start, check:

  * IP, port, and path correctness
  * Network reachability
  * FFmpeg/PyAV installation
* Some cameras require vendor-specific channels/subtypes — check your camera’s documentation.
* UDP transport may have lower latency but is less reliable than TCP.
