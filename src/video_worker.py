# video_worker.py

import threading, time
from typing import Optional
import av

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

# Robust exception import across PyAV versions
try:
    from av.error import FFError as AvError
except Exception:
    try:
        from av.error import Error as AvError
    except Exception:
        AvError = Exception  # last-resort fallback

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
