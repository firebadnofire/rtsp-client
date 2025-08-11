import sys, time, threading, json
from typing import Optional
import numpy as np
import av  # PyAV

# Robust exception import across PyAV versions
try:
    from av.error import FFError as AvError
except Exception:
    try:
        from av.error import Error as AvError
    except Exception:
        AvError = Exception  # last-resort fallback

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QImage, QPixmap, QCursor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QFormLayout
)


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


class FullscreenVideo(QWidget):
    """A borderless fullscreen window that shows only the video feed."""

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
        lay.addWidget(self.video_lbl, 1)

        self._last_pix: Optional[QPixmap] = None
        self._cursor_hidden = False

    def on_frame(self, qimg: QImage):
        if not self.isVisible():
            return
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return
        self._last_pix = pix
        self._rescale()

    def _rescale(self):
        pm = self._last_pix
        if pm is None or pm.isNull():
            return
        self.video_lbl.setPixmap(pm.scaled(
            self.video_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, e):
        self._rescale()
        super().resizeEvent(e)

    def keyPressEvent(self, e):
        # Exit on Esc or F11
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11, Qt.Key.Key_Q):
            self.hide()
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        # Double-click to exit
        self.hide()
        e.accept()

    def showEvent(self, e):
        # Hide cursor in fullscreen for cleaner view
        if not self._cursor_hidden:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
            self._cursor_hidden = True
        super().showEvent(e)

    def hideEvent(self, e):
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        super().hideEvent(e)


class RtspApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RTSP Viewer (PyQt6 + PyAV)")
        self.resize(1100, 750)

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
        self.subtype_combo.addItems(["0", "1", "2"])

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
        self.fullscreen_btn = QPushButton("Fullscreen")
        self.save_btn = QPushButton("Save Config…")
        self.load_btn = QPushButton("Load Config…")

        self.status_lbl = QLabel("Idle")
        self.video_lbl = QLabel()
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setText("No video")
        self.video_lbl.setStyleSheet("background:#111; color:#888;")

        form = QFormLayout()
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

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("RTSP URL Preview"))
        row3.addStretch(1)
        row3.addWidget(self.save_btn)
        row3.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addWidget(self.url_preview)
        layout.addWidget(self.video_lbl, 1)
        layout.addWidget(self.status_lbl)

        # ----- Fullscreen window (separate, video-only) -----
        self.fullwin = FullscreenVideo()

        self.worker = VideoWorker()
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.frame_ready.connect(self.fullwin.on_frame)  # feed fullscreen window too
        self.worker.status.connect(self.status_lbl.setText)
        self.worker.stopped.connect(self.on_stopped)

        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snap_btn.clicked.connect(self.snapshot)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.save_btn.clicked.connect(self.save_config)
        self.load_btn.clicked.connect(self.load_config)

        for w in [
            self.user_edit, self.pass_edit, self.host_edit, self.port_spin,
            self.path_edit, self.channel_combo, self.subtype_combo
        ]:
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.update_preview)
            else:
                w.textChanged.connect(self.update_preview) if isinstance(w, QLineEdit) else w.valueChanged.connect(self.update_preview)
        self.update_preview()

    def build_url(self, include_password: bool = True) -> str:
        user = self.user_edit.text().strip()
        pwd = self.pass_edit.text().strip()
        host = self.host_edit.text().strip()
        port = int(self.port_spin.value())
        path = self.path_edit.text().strip() or "/cam/realmonitor"
        if not path.startswith("/"):
            path = "/" + path
        channel = self.channel_combo.currentText()
        subtype = self.subtype_combo.currentText()
        auth = ""
        if user:
            auth = user
            if include_password and pwd:
                auth += f":{pwd}"
            auth += "@"
        url = f"rtsp://{auth}{host}:{port}{path}?channel={channel}&subtype={subtype}"
        return url

    def update_preview(self):
        preview = self.build_url(include_password=False)
        self.url_preview.setText(preview)

    def start_stream(self):
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "Missing host", "Enter the camera IP or hostname.")
            return
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "Missing path", "Enter the base path (e.g., /cam/realmonitor).")
            return
        url = self.build_url(include_password=True)
        self.worker.start(url, self.transport.currentText(), self.latency.value())
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.snap_btn.setEnabled(True)
        self.status_lbl.setText("Starting…")

    def stop_stream(self):
        self.worker.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.snap_btn.setEnabled(False)
        self.status_lbl.setText("Stopped")

    def on_frame(self, qimg: QImage):
        pix = QPixmap.fromImage(qimg)
        if not pix.isNull():
            self.video_lbl.setPixmap(pix.scaled(
                self.video_lbl.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event):
        pm = self.video_lbl.pixmap()
        if pm and not pm.isNull():
            self.video_lbl.setPixmap(pm.scaled(
                self.video_lbl.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        super().resizeEvent(event)

    def toggle_fullscreen(self):
        # Show/hide separate fullscreen, video-only window
        if self.fullwin.isVisible():
            self.fullwin.hide()
        else:
            self.fullwin.showFullScreen()

    # ---- Config save/load ----
    def save_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Camera Config",
            "camera.json",
            "JSON (*.json)"
        )
        if not path:
            return
        cfg = {
            "user": self.user_edit.text(),
            "pass": self.pass_edit.text(),
            "ip": self.host_edit.text(),
            "port": int(self.port_spin.value()),
            "slug": self.path_edit.text().strip() or "/cam/realmonitor",
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.status_lbl.setText(f"Saved config → {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Camera Config",
            "",
            "JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return

        # Set fields if present
        self.user_edit.setText(str(cfg.get("user", "")))
        self.pass_edit.setText(str(cfg.get("pass", "")))
        self.host_edit.setText(str(cfg.get("ip", "")))
        if "port" in cfg:
            try:
                self.port_spin.setValue(int(cfg["port"]))
            except Exception:
                pass
        slug = str(cfg.get("slug", "/cam/realmonitor"))
        self.path_edit.setText(slug)

        self.update_preview()
        self.status_lbl.setText(f"Loaded config ← {path}")

    def on_stopped(self):
        pass

    def snapshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", "snapshot.png", "PNG (*.png);;JPEG (*.jpg)"
        )
        if path:
            ok = self.worker.save_snapshot(path)
            if not ok:
                QMessageBox.warning(self, "Snapshot", "No frame available yet.")

    def closeEvent(self, e):
        self.worker.stop()
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = RtspApp()
    w.show()
    sys.exit(app.exec())
