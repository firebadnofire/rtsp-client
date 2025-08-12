import sys, time, threading, json
from typing import Optional, List, Dict, Any
import numpy as np
import av  # PyAV

# ---------------------- Configurable display targets ----------------------
# Pane size is fixed to keep feeds from "growing" as new frames arrive.
# 960x540 (16:9) gives a 1920x1080 total canvas for the 2x2 grid — a good fit
# while still downscaling cleanly from 4K cameras.
PANE_TARGET_W, PANE_TARGET_H = 960, 540

# Robust exception import across PyAV versions
try:
    from av.error import FFError as AvError
except Exception:
    try:
        from av.error import Error as AvError
    except Exception:
        AvError = Exception  # last-resort fallback

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QSize, QPoint, QRect
from PyQt6.QtGui import QImage, QPixmap, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QFormLayout, QGridLayout, QFrame
)


# ---------------------- Worker ----------------------
class VideoWorker(QObject):
    frame_ready = pyqtSignal(QImage)
    status = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_qimage: Optional[QImage] = None

    def start(self, url: str, transport: str, latency_ms: int):
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(url, transport, latency_ms), daemon=True
        )
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=2)
        self._thread = None

    def save_snapshot(self, path: str) -> bool:
        if self._last_qimage is None:
            return False
        return self._last_qimage.save(path)

    def _run(self, url: str, transport: str, latency_ms: int):
        opts = {
            "rtsp_transport": transport,
            "stimeout": str(5_000_000),
            "max_delay": str(max(0, latency_ms) * 1000),
        }

        while not self._stop.is_set():
            try:
                self.status.emit("Connecting…")
                with av.open(url, options=opts, timeout=5.0) as container:
                    stream = next((s for s in container.streams if s.type == "video"), None)
                    if stream is None:
                        self.status.emit("No video stream found")
                        break
                    stream.thread_type = "AUTO"
                    try:
                        stream.codec_context.skip_frame = "NONKEY"
                    except Exception:
                        pass
                    self.status.emit("Playing")
                    for frame in container.decode(stream):
                        if self._stop.is_set():
                            break
                        # Convert frame to RGB and keep a copy
                        img = frame.to_ndarray(format="rgb24")
                        h, w, _ = img.shape
                        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888)
                        qimg = qimg.copy()
                        self._last_qimage = qimg
                        self.frame_ready.emit(qimg)
                if self._stop.is_set():
                    break
                self.status.emit("Stream ended, reconnecting in 2s…")
                time.sleep(2)
            except AvError as e:
                if self._stop.is_set():
                    break
                self.status.emit(f"FFmpeg/PyAV error: {e}; retrying in 2s…")
                time.sleep(2)
            except Exception as e:
                if self._stop.is_set():
                    break
                self.status.emit(f"Error: {e}; retrying in 2s…")
                time.sleep(2)
        self.stopped.emit()


# ---------------------- Video Pane ----------------------
class VideoPane(QFrame):
    clicked = pyqtSignal(int)  # emits panel index

    def __init__(self, index: int, title: str = "", target_size: QSize = QSize(PANE_TARGET_W, PANE_TARGET_H)):
        super().__init__()
        self.index = index
        self.setObjectName(f"pane{index}")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { background: #111; border: 1px solid #333; }")

        self._last_pix: Optional[QPixmap] = None
        self._target_size = target_size

        # Title bar
        self.title = QLabel(title or f"Feed {index+1}")
        self.title.setStyleSheet("color:#bbb; background: #222; padding:4px 8px;")
        self.title.setFont(QFont("Monospace", 9))

        # Video label — fixed size to stop growth
        self.video_lbl = QLabel()
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setStyleSheet("background:#000; color:#666;")
        self.video_lbl.setText("No video")
        self.video_lbl.setFixedSize(self._target_size)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.title)
        # center the fixed-size video label
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(self.video_lbl)
        wrap.addStretch(1)
        v.addLayout(wrap, 1)

    def sizeHint(self) -> QSize:
        # Include title height overhead
        return QSize(self._target_size.width(), self._target_size.height() + 22)

    def set_active(self, active: bool):
        if active:
            self.setStyleSheet("QFrame { background:#111; border: 2px solid #4da3ff; }")
            self.title.setStyleSheet("color:#e6f1ff; background:#1b2a3a; padding:4px 8px;")
        else:
            self.setStyleSheet("QFrame { background:#111; border: 1px solid #333; }")
            self.title.setStyleSheet("color:#bbb; background:#222; padding:4px 8px;")

    def on_frame(self, qimg: QImage):
        if qimg.isNull():
            return
        # Always scale to the fixed target size to avoid growth
        pm = QPixmap.fromImage(qimg)
        if pm.isNull():
            return
        scaled = pm.scaled(self._target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._last_pix = scaled
        self.video_lbl.setPixmap(scaled)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(e)


# ---------------------- Fullscreen Window ----------------------
class FullscreenVideo(QWidget):
    """A borderless fullscreen window that shows only the video feed for the active panel.

    This version uses the *actual display size* and scales every frame to the
    current window size (typically the full-screen size), instead of a fixed target.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("RTSP — Fullscreen")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background:black;")

        self.video_lbl = QLabel(self)
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setStyleSheet("background:black;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video_lbl)

        self._cursor_hidden = False

    def _target_size(self) -> QSize:
        # Use the actual current window size, which is the screen size in fullscreen
        return self.size()

    def on_frame(self, qimg: QImage):
        if not self.isVisible() or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        if pm.isNull():
            return
        # Scale to *current* window size, preserving aspect ratio
        scaled = pm.scaled(self._target_size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.video_lbl.setPixmap(scaled)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11, Qt.Key.Key_Q):
            self.hide()
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.hide()
        e.accept()

    def showEvent(self, e):
        if not self._cursor_hidden:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
            self._cursor_hidden = True
        super().showEvent(e)

    def hideEvent(self, e):
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        super().hideEvent(e)


# ---------------------- Main App ----------------------
class RtspApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RTSP Viewer — 4 Panel (Fixed Res)")
        # Default window sized to fit 2x2 panes at the fixed target (plus UI)
        self.resize(PANE_TARGET_W * 2 + 80, PANE_TARGET_H * 2 + 300)

        # Per-panel state (what the UI edits for the *active* panel)
        self.panel_states: List[Dict[str, Any]] = [
            {
                "user": "",
                "pass": "",
                "ip": "",
                "port": 554,
                "slug": "/cam/realmonitor",
                "channel": "1",
                "subtype": "0",
                "transport": "tcp",
                "latency": 100,
                "running": False,
                "title": f"Feed {i+1}",
            }
            for i in range(4)
        ]

        # Workers
        self.workers: List[VideoWorker] = [VideoWorker() for _ in range(4)]

        # --- Controls (apply to active panel) ---
        self.user_edit = QLineEdit(self)
        self.user_edit.setPlaceholderText("user")
        self.pass_edit = QLineEdit(self)
        self.pass_edit.setPlaceholderText("pass")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.host_edit = QLineEdit(self)
        self.host_edit.setPlaceholderText("192.168.1.10")
        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(554)

        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("/cam/realmonitor")
        self.path_edit.setText("/cam/realmonitor")

        self.channel_combo = QComboBox(self)
        self.channel_combo.addItems([str(i) for i in range(1, 17)])
        self.subtype_combo = QComboBox(self)
        self.subtype_combo.addItems(["0", "1", "2"])  # vendor-dependent

        self.transport = QComboBox(self)
        self.transport.addItems(["tcp", "udp"])
        self.latency = QSpinBox(self)
        self.latency.setRange(0, 2000)
        self.latency.setValue(100)
        self.latency.setSuffix(" ms")

        self.url_preview = QLineEdit(self)
        self.url_preview.setReadOnly(True)
        self.url_preview.setPlaceholderText("rtsp://user:pass@host:554/cam/realmonitor?channel=1&subtype=0")

        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.snap_btn = QPushButton("Snapshot")
        self.snap_btn.setEnabled(False)
        self.fullscreen_btn = QPushButton("Fullscreen (active)")
        self.save_btn = QPushButton("Save Config…")
        self.load_btn = QPushButton("Load Config…")

        # New: Start/Stop all cameras
        self.start_all_btn = QPushButton("Start All")
        self.stop_all_btn = QPushButton("Stop All")

        self.status_lbl = QLabel("Idle")

        # 2x2 grid of panes
        self.panes: List[VideoPane] = [VideoPane(i) for i in range(4)]
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(self.panes[0], 0, 0)
        grid.addWidget(self.panes[1], 0, 1)
        grid.addWidget(self.panes[2], 1, 0)
        grid.addWidget(self.panes[3], 1, 1)

        # Connect workers to panes
        for i, w in enumerate(self.workers):
            w.frame_ready.connect(self.panes[i].on_frame)
            w.status.connect(self._make_status_updater(i))

        # Active panel tracking
        self.active_index: int = 0
        for p in self.panes:
            p.clicked.connect(self.set_active_panel)
        self._update_active_styles()

        # Feed UI changes to preview
        for w in [
            self.user_edit, self.pass_edit, self.host_edit, self.port_spin,
            self.path_edit, self.channel_combo, self.subtype_combo, self.transport, self.latency
        ]:
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.update_preview)
            else:
                getattr(w, 'textChanged', None) and w.textChanged.connect(self.update_preview)
                getattr(w, 'valueChanged', None) and w.valueChanged.connect(self.update_preview)

        # Top form
        form = QFormLayout()
        form.addRow("Active panel", QLabel("Click a feed to select (blue border)"))
        form.addRow("User", self.user_edit)
        form.addRow("Pass", self.pass_edit)
        form.addRow("IP / Host", self.host_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("Base Path (slug)", self.path_edit)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Channel"))
        row2.addWidget(self.channel_combo)
        row2.addWidget(QLabel("Subtype"))
        row2.addWidget(self.subtype_combo)
        row2.addSpacing(20)
        row2.addWidget(QLabel("Transport"))
        row2.addWidget(self.transport)
        row2.addWidget(QLabel("Latency"))
        row2.addWidget(self.latency)
        row2.addStretch(1)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.snap_btn)
        row2.addWidget(self.fullscreen_btn)
        # New: all-cam controls
        row2.addWidget(self.start_all_btn)
        row2.addWidget(self.stop_all_btn)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("RTSP URL Preview"))
        row3.addStretch(1)
        row3.addWidget(self.save_btn)
        row3.addWidget(self.load_btn)

        # Main vertical layout
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addWidget(self.url_preview)
        layout.addLayout(grid, 1)
        layout.addWidget(self.status_lbl)

        # Fullscreen window
        self.fullwin = FullscreenVideo()
        self._fullscreen_source_index: Optional[int] = None

        # Wire control buttons
        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snap_btn.clicked.connect(self.snapshot)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.save_btn.clicked.connect(self.save_config)
        self.load_btn.clicked.connect(self.load_config)
        # New: all-cam controls
        self.start_all_btn.clicked.connect(self.start_all_streams)
        self.stop_all_btn.clicked.connect(self.stop_all_streams)

        # Initialize UI from active panel state
        self._sync_ui_from_state(self.active_index)
        self.update_preview()

    # ---------------- State / URL helpers ----------------
    def build_url_from_state(self, st: Dict[str, Any], include_password: bool = True) -> str:
        user = (st.get("user") or "").strip()
        pwd = (st.get("pass") or "").strip()
        host = (st.get("ip") or "").strip()
        port = int(st.get("port") or 554)
        path = (st.get("slug") or "/cam/realmonitor").strip()
        if not path.startswith("/"):
            path = "/" + path
        channel = st.get("channel", "1")
        subtype = st.get("subtype", "0")
        auth = ""
        if user:
            auth = user
            if include_password and pwd:
                auth += f":{pwd}"
            auth += "@"
        return f"rtsp://{auth}{host}:{port}{path}?channel={channel}&subtype={subtype}"

    def _sync_ui_from_state(self, idx: int):
        st = self.panel_states[idx]
        self.user_edit.setText(st.get("user", ""))
        self.pass_edit.setText(st.get("pass", ""))
        self.host_edit.setText(st.get("ip", ""))
        self.port_spin.setValue(int(st.get("port", 554)))
        self.path_edit.setText(st.get("slug", "/cam/realmonitor"))
        self._set_combo_value(self.channel_combo, st.get("channel", "1"))
        self._set_combo_value(self.subtype_combo, st.get("subtype", "0"))
        self._set_combo_value(self.transport, st.get("transport", "tcp"))
        self.latency.setValue(int(st.get("latency", 100)))
        self.update_preview()
        self._update_buttons_enabled()

    def _sync_state_from_ui(self, idx: int):
        st = self.panel_states[idx]
        st["user"] = self.user_edit.text().strip()
        st["pass"] = self.pass_edit.text().strip()
        st["ip"] = self.host_edit.text().strip()
        st["port"] = int(self.port_spin.value())
        st["slug"] = self.path_edit.text().strip() or "/cam/realmonitor"
        st["channel"] = self.channel_combo.currentText()
        st["subtype"] = self.subtype_combo.currentText()
        st["transport"] = self.transport.currentText()
        st["latency"] = int(self.latency.value())

    def _set_combo_value(self, combo: QComboBox, val: str):
        i = combo.findText(val)
        combo.setCurrentIndex(max(0, i))

    def update_preview(self):
        st = self.panel_states[self.active_index].copy()
        st["user"] = self.user_edit.text().strip()
        st["pass"] = self.pass_edit.text().strip()
        st["ip"] = self.host_edit.text().strip()
        st["port"] = int(self.port_spin.value())
        st["slug"] = self.path_edit.text().strip() or "/cam/realmonitor"
        st["channel"] = self.channel_combo.currentText()
        st["subtype"] = self.subtype_combo.currentText()
        url = self.build_url_from_state(st, include_password=False)
        self.url_preview.setText(url)

    # ---------------- Active panel handling ----------------
    def set_active_panel(self, idx: int):
        if idx == self.active_index:
            return
        self._sync_state_from_ui(self.active_index)
        self.active_index = idx
        self._update_active_styles()
        self._sync_ui_from_state(idx)
        if self.fullwin.isVisible():
            self._connect_fullscreen_to(idx)

    def _update_active_styles(self):
        for i, p in enumerate(self.panes):
            p.set_active(i == self.active_index)

    # ---------------- Start/Stop/Snapshot ----------------
    def start_stream(self):
        self._sync_state_from_ui(self.active_index)
        st = self.panel_states[self.active_index]

        if not st.get("ip"):
            QMessageBox.warning(self, "Missing host", "Enter the camera IP or hostname.")
            return
        if not st.get("slug"):
            QMessageBox.warning(self, "Missing path", "Enter the base path (e.g., /cam/realmonitor).")
            return

        url = self.build_url_from_state(st, include_password=True)
        w = self.workers[self.active_index]
        w.start(url, st.get("transport", "tcp"), int(st.get("latency", 100)))
        st["running"] = True
        self.status_lbl.setText(f"Panel {self.active_index+1}: Starting…")
        self._update_buttons_enabled()

    def stop_stream(self):
        w = self.workers[self.active_index]
        w.stop()
        self.panel_states[self.active_index]["running"] = False
        self.status_lbl.setText(f"Panel {self.active_index+1}: Stopped")
        self._update_buttons_enabled()

    def snapshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", f"panel{self.active_index+1}_snapshot.png", "PNG (*.png);;JPEG (*.jpg)"
        )
        if path:
            ok = self.workers[self.active_index].save_snapshot(path)
            if not ok:
                QMessageBox.warning(self, "Snapshot", "No frame available yet.")

    def _update_buttons_enabled(self):
        running = self.panel_states[self.active_index].get("running", False)
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.snap_btn.setEnabled(True)
    # ---------------- All-cam controls ----------------
    def start_all_streams(self):
        """Start all panels that have enough info configured."""
        # Ensure the active panel's latest UI values are captured
        self._sync_state_from_ui(self.active_index)
        started, skipped = [], []
        for i in range(4):
            st = self.panel_states[i]
            if not st.get("ip") or not st.get("slug"):
                skipped.append(i + 1)
                continue
            try:
                url = self.build_url_from_state(st, include_password=True)
                self.workers[i].start(url, st.get("transport", "tcp"), int(st.get("latency", 100)))
                st["running"] = True
                started.append(i + 1)
            except Exception:
                skipped.append(i + 1)
        self._update_buttons_enabled()
        if started and skipped:
            self.status_lbl.setText(f"All: started {started}; skipped {skipped}")
        elif started:
            self.status_lbl.setText(f"All: started {started}")
        elif skipped:
            self.status_lbl.setText(f"All: skipped {skipped} (missing host/path)")
        else:
            self.status_lbl.setText("All: nothing to start")

    def stop_all_streams(self):
        for i, w in enumerate(self.workers):
            try:
                w.stop()
            finally:
                self.panel_states[i]["running"] = False
        self._update_buttons_enabled()
        self.status_lbl.setText("All: stopped")

    # ---------------- Fullscreen ----------------
    def toggle_fullscreen(self):
        if self.fullwin.isVisible():
            self.fullwin.hide()
        else:
            self._connect_fullscreen_to(self.active_index)
            # Show *true* fullscreen; scaling happens to the window's actual size
            self.fullwin.showFullScreen()

    def _connect_fullscreen_to(self, idx: int):
        if hasattr(self, "_fullscreen_source_index") and self._fullscreen_source_index is not None:
            try:
                self.workers[self._fullscreen_source_index].frame_ready.disconnect(self.fullwin.on_frame)
            except Exception:
                pass
        self.workers[idx].frame_ready.connect(self.fullwin.on_frame)
        self._fullscreen_source_index = idx

    # ---------------- Save/Load ----------------
    def _window_geometry(self) -> Dict[str, int]:
        g: QRect = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def _apply_window_geometry(self, geom: Dict[str, int]):
        try:
            x, y, w, h = geom["x"], geom["y"], geom["w"], geom["h"]
            self.setGeometry(QRect(x, y, w, h))
        except Exception:
            pass

    def save_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save 4-Panel Config",
            "cameras.json",
            "JSON (*.json)"
        )
        if not path:
            return

        # Always sync the *active* panel from UI; other panels already hold their own latest states
        self._sync_state_from_ui(self.active_index)

        data = {
            "version": 1,
            "ui": {
                "active_index": self.active_index,
                "window": self._window_geometry(),
                "fullscreen_visible": self.fullwin.isVisible(),
            },
            # Save *all* four panel configs, not just the active one
            "panels": self.panel_states,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.status_lbl.setText(f"Saved config → {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load 4-Panel Config",
            "",
            "JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return

        panels = data.get("panels")
        if not isinstance(panels, list) or len(panels) != 4:
            QMessageBox.critical(self, "Load failed", "Config must contain a 'panels' list of length 4.")
            return

        # Stop current workers before applying new states
        for i in range(4):
            self.workers[i].stop()

        self.panel_states = panels

        # Apply UI meta (active panel, window geometry)
        ui_meta = data.get("ui", {})
        if isinstance(ui_meta, dict):
            geom = ui_meta.get("window")
            if isinstance(geom, dict):
                self._apply_window_geometry(geom)
            new_active = ui_meta.get("active_index")
            if isinstance(new_active, int) and 0 <= new_active < 4:
                self.active_index = new_active

        # Refresh panes and active UI
        self._update_active_styles()
        self._sync_ui_from_state(self.active_index)

        # Optionally (re)start any panels that were saved with running=True
        restarted = []
        for i, st in enumerate(self.panel_states):
            if st.get("running"):
                url = self.build_url_from_state(st, include_password=True)
                self.workers[i].start(url, st.get("transport", "tcp"), int(st.get("latency", 100)))
                restarted.append(i + 1)
        if restarted:
            self.status_lbl.setText(f"Loaded config ← {path} (auto-started: {restarted})")
        else:
            self.status_lbl.setText(f"Loaded config ← {path}")

    # ---------------- Close ----------------
    def closeEvent(self, e):
        for w in self.workers:
            w.stop()
        super().closeEvent(e)

    # ---------------- Helpers ----------------
    def _make_status_updater(self, idx: int):
        def _set(text: str):
            prefix = f"P{idx+1}: "
            if idx == self.active_index:
                self.status_lbl.setText(prefix + text)
        return _set


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = RtspApp()
    w.show()
    sys.exit(app.exec())
