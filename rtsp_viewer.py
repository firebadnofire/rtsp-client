import sys, time, threading
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
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox
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
        # FFmpeg/RTSP options
        opts = {
            "rtsp_transport": transport,           # "tcp" or "udp"
            "stimeout": str(5_000_000),            # 5s connect/read timeout (µs)
            "max_delay": str(max(0, latency_ms) * 1000),  # decoder queue (µs)
        }

        while not self._stop.is_set():
            try:
                self.status.emit("Connecting…")
                with av.open(url, options=opts, timeout=5.0) as container:
                    stream = next((s for s in container.streams if s.type == "video"), None)
                    if stream is None:
                        self.status.emit("No video stream found")
                        break

                    # Low-latency decode hints
                    stream.thread_type = "AUTO"
                    # Skip non-key frames if we're falling behind
                    try:
                        stream.codec_context.skip_frame = "NONKEY"
                    except Exception:
                        pass  # some codecs/versions may not expose this

                    self.status.emit("Playing")
                    for frame in container.decode(stream):
                        if self._stop.is_set():
                            break
                        img = frame.to_ndarray(format="rgb24")
                        h, w, _ = img.shape
                        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888)
                        qimg = qimg.copy()  # detach from numpy buffer
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


class RtspApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RTSP Viewer (PyQt6 + PyAV)")
        self.resize(1000, 650)

        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText("rtsp://user:pass@host:554/stream")

        self.transport = QComboBox(self)
        self.transport.addItems(["tcp", "udp"])

        self.latency = QSpinBox(self)
        self.latency.setRange(0, 2000)
        self.latency.setValue(100)
        self.latency.setSuffix(" ms")

        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.snap_btn = QPushButton("Snapshot")
        self.snap_btn.setEnabled(False)

        self.status_lbl = QLabel("Idle")
        self.video_lbl = QLabel()
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setText("No video")
        self.video_lbl.setStyleSheet("background:#111; color:#888;")

        top = QHBoxLayout()
        top.addWidget(QLabel("RTSP:"))
        top.addWidget(self.url_edit, 1)
        top.addWidget(QLabel("Transport"))
        top.addWidget(self.transport)
        top.addWidget(QLabel("Latency"))
        top.addWidget(self.latency)
        top.addWidget(self.start_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.snap_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.video_lbl, 1)
        layout.addWidget(self.status_lbl)

        self.worker = VideoWorker()
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.status.connect(self.status_lbl.setText)
        self.worker.stopped.connect(self.on_stopped)

        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snap_btn.clicked.connect(self.snapshot)

    def start_stream(self):
        url = self.url_edit.text().strip()
        if not url.startswith("rtsp://"):
            QMessageBox.warning(self, "Bad URL", "Enter a valid rtsp:// URL")
            return
        self.worker.start(url, self.transport.currentText(), self.latency.value())
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.snap_btn.setEnabled(True)

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
