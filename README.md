# RTSP Viewer — 4-Panel Fixed Resolution

A PyQt6 + PyAV-based desktop application for viewing and managing up to **four simultaneous RTSP camera feeds** in a fixed-resolution 2×2 grid layout.
Supports per-panel configuration, fullscreen viewing, snapshots, and persistent config saving/loading.

---

## Features

* **Four camera feeds** in a fixed 960×540 panel layout (fits neatly into a 1920×1080 grid).
* **Start/Stop per panel** or **start/stop all at once**.
* **Live RTSP URL preview** as you edit connection settings.
* **TCP/UDP transport selection** with configurable latency.
* **Snapshot capture** to PNG/JPEG.
* **Fullscreen mode** for the active panel.
* **Save and load configuration** (`.json`), including:

  * All panel connection settings
  * Window geometry
  * Running states (with optional auto-restart on load)
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
venv/bin/python rtsp_viewer.py
```

You’ll see a **2×2 camera panel grid**.
Click a panel to make it active (blue border). Use the form fields to configure that panel’s connection.

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
| **Fullscreen (active)** | View active feed fullscreen (Esc/F11/Q to exit)             |
| **Start All**           | Start all configured streams                                |
| **Stop All**            | Stop all streams                                            |
| **Save Config…**        | Save all panel settings + UI state                          |
| **Load Config…**        | Load panel settings + optionally auto-start running streams |

---

### Configuration Files

* **config.json** — optional pre-saved configuration.
* **rtsp-url.txt** — example RTSP URLs or notes (not used automatically).
* Saved configs store:

  * Per-panel credentials, host, port, slug, channel, subtype, transport, latency, and running state.
  * Active panel index and window size.
  * Fullscreen visibility.

---

## Directory Structure

```
rtsp/
├── config.json          # Optional saved configuration
├── LICENSE              # License file
├── README.md            # This file
├── requirements.txt     # Dependencies list
├── rtsp-url.txt         # Example RTSP URL(s) or notes
├── rtsp_viewer.py       # Main application
└── venv/                # (Optional) Python virtual environment
```

---

## Keyboard & Mouse Shortcuts

* **Click panel** — select active panel.
* **Esc / F11 / Q** — exit fullscreen.

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
