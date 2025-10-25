# main.py

import sys
import json
import threading
import time
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt6.QtGui import QImage, QPixmap, QCursor, QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QFormLayout, QGridLayout, QFrame,
    QSizePolicy, QListView, QCheckBox, QDialog, QTextEdit, QSlider
)

# ============================================================================
# Configuration Constants (from config.py)
# ============================================================================

# Pane size is fixed to keep feeds from "growing" as new frames arrive.
# 960x540 (16:9) gives a 1920x1080 total canvas for the 2x2 grid — a good fit
# while still downscaling cleanly from 4K cameras.
PANE_TARGET_W, PANE_TARGET_H = 960, 540

# Default camera connection parameters can also go here if desired
DEFAULT_CAMERA_SLUG = "/cam/realmonitor"
DEFAULT_TRANSPORT = "tcp"
DEFAULT_LATENCY_MS = 100

# ============================================================================
# VideoWorker Class (from video_worker.py)
# ============================================================================

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
                    
                    # Removed the line to skip non-keyframes for a smoother stream.
                    # try:
                    #     stream.codec_context.skip_frame = "NONKEY"
                    # except Exception:
                    #     pass
                    
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


class VideoRecorder:
    """Handles video recording to file using PyAV."""
    
    def __init__(self):
        self._container: Optional[av.container.OutputContainer] = None
        self._stream: Optional[av.stream.Stream] = None
        self._lock = threading.Lock()
        self._is_recording = False
        self._output_path: Optional[str] = None
        self._frame_count = 0
        
    def start_recording(self, output_path: str, width: int, height: int, fps: int = 25) -> bool:
        """Start recording video to the specified path."""
        with self._lock:
            if self._is_recording:
                return False
            
            try:
                self._output_path = output_path
                self._container = av.open(output_path, mode='w')
                self._stream = self._container.add_stream('h264', rate=fps)
                self._stream.width = width
                self._stream.height = height
                self._stream.pix_fmt = 'yuv420p'
                # Use medium preset for balance between speed and quality
                self._stream.options = {'preset': 'medium', 'crf': '23'}
                self._is_recording = True
                self._frame_count = 0
                return True
            except Exception as e:
                print(f"Failed to start recording: {e}")
                self._cleanup()
                return False
    
    def write_frame(self, qimage: QImage) -> bool:
        """Write a QImage frame to the video file."""
        with self._lock:
            if not self._is_recording or self._stream is None:
                return False
            
            try:
                # Convert QImage to numpy array
                width = qimage.width()
                height = qimage.height()
                ptr = qimage.bits()
                ptr.setsize(height * width * 4)  # 4 bytes per pixel for RGB888
                arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 4))
                
                # Convert RGBA to RGB if needed
                if arr.shape[2] == 4:
                    arr = arr[:, :, :3]
                
                # Create video frame
                frame = av.VideoFrame.from_ndarray(arr, format='rgb24')
                
                # Encode and write
                for packet in self._stream.encode(frame):
                    self._container.mux(packet)
                
                self._frame_count += 1
                return True
            except Exception as e:
                print(f"Failed to write frame: {e}")
                return False
    
    def stop_recording(self) -> Optional[str]:
        """Stop recording and return the output path."""
        with self._lock:
            if not self._is_recording:
                return None
            
            output_path = self._output_path
            
            try:
                # Flush remaining frames
                if self._stream is not None:
                    for packet in self._stream.encode():
                        self._container.mux(packet)
            except Exception as e:
                print(f"Error flushing frames: {e}")
            
            self._cleanup()
            return output_path if self._frame_count > 0 else None
    
    def is_recording(self) -> bool:
        """Check if currently recording."""
        with self._lock:
            return self._is_recording
    
    def _cleanup(self):
        """Clean up resources."""
        try:
            if self._container is not None:
                self._container.close()
        except Exception as e:
            print(f"Error closing container: {e}")
        
        self._container = None
        self._stream = None
        self._is_recording = False
        self._output_path = None
        self._frame_count = 0


# ============================================================================
# Helper Functions
# ============================================================================

def overlay_text_on_qimage(qimage: QImage, text: str, position: str = "top-left") -> QImage:
    """
    Overlay text on a QImage and return a new QImage.
    
    Args:
        qimage: The source QImage
        text: Text to overlay
        position: Position of the text ("top-left", "top-right", "bottom-left", "bottom-right")
    
    Returns:
        New QImage with text overlay
    """
    if qimage.isNull() or not text:
        return qimage
    
    # Convert QImage to PIL Image
    width = qimage.width()
    height = qimage.height()
    ptr = qimage.bits()
    ptr.setsize(height * width * 4)
    
    # Create PIL Image from QImage data
    pil_image = Image.frombytes('RGBA', (width, height), ptr.asarray())
    
    # Create drawing context
    draw = ImageDraw.Draw(pil_image)
    
    # Try to use a nice font, fall back to default if not available
    try:
        font_size = max(16, int(min(width, height) * 0.04))
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    
    # Get text bounding box
    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    else:
        # Approximate size for default font
        text_width = len(text) * 8
        text_height = 12
    
    # Calculate position
    padding = 10
    if position == "top-left":
        x, y = padding, padding
    elif position == "top-right":
        x, y = width - text_width - padding, padding
    elif position == "bottom-left":
        x, y = padding, height - text_height - padding * 2
    elif position == "bottom-right":
        x, y = width - text_width - padding, height - text_height - padding * 2
    else:
        x, y = padding, padding
    
    # Draw background rectangle
    bg_padding = 5
    draw.rectangle(
        [x - bg_padding, y - bg_padding, x + text_width + bg_padding, y + text_height + bg_padding],
        fill=(0, 0, 0, 180)
    )
    
    # Draw text
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    
    # Convert back to QImage
    data = pil_image.tobytes('raw', 'RGBA')
    result = QImage(data, width, height, QImage.Format.Format_RGBA8888)
    return result.copy()


# ============================================================================
# Widget Classes (from widgets.py)
# ============================================================================

class VideoPane(QFrame):
    clicked = pyqtSignal(int)

    def __init__(
        self,
        index: int,
        title: str = "",
        target_size: QSize = QSize(PANE_TARGET_W, PANE_TARGET_H),
        scale: float = 1.0,
    ):
        super().__init__()
        self.index = index
        # --- MODIFIED: Removed object name, will style based on class ---
        self.setFrameShape(QFrame.Shape.StyledPanel)

        # --- MODIFIED: Removed inline stylesheet ---
        self._last_pix: Optional[QPixmap] = None
        self._target_size = target_size
        self._scale = scale

        # Create title container with recording indicator
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(max(4, int(6 * self._scale)))
        
        self.title = QLabel(title or f"Feed {index+1}")
        # --- MODIFIED: Added object name for specific styling ---
        self.title.setObjectName("pane_title")
        
        self.recording_indicator = QLabel("⏺")
        self.recording_indicator.setObjectName("recording_indicator")
        self.recording_indicator.setStyleSheet("color: red; font-weight: bold;")
        self.recording_indicator.hide()
        
        title_layout.addWidget(self.title, 1)
        title_layout.addWidget(self.recording_indicator)

        self.video_lbl = QLabel()
        # --- MODIFIED: Added object name for specific styling ---
        self.video_lbl.setObjectName("video_lbl")
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setText("No video")
        self.video_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_lbl.setMinimumSize(self._target_size)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(max(0, int(4 * self._scale)))
        v.addWidget(title_container)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)
        wrap.addWidget(self.video_lbl, 1)
        v.addLayout(wrap, 1)

    def set_recording(self, recording: bool):
        """Show or hide the recording indicator."""
        if recording:
            self.recording_indicator.show()
        else:
            self.recording_indicator.hide()

    def sizeHint(self) -> QSize:
        # Manually calculate hint based on layout to be safe
        title_h = self.title.sizeHint().height()
        return QSize(self._target_size.width(), self._target_size.height() + title_h)

    def set_active(self, active: bool):
        """
        --- MODIFIED: Uses properties instead of stylesheets ---
        Sets a property on the widget which the main stylesheet can use
        to change its appearance (e.g., border color).
        """
        self.setProperty("active", active)
        # Re-polish the widget to force a style update
        self.style().unpolish(self)
        self.style().polish(self)

    def on_frame(self, qimg: QImage):
        if qimg.isNull(): return
        pm = QPixmap.fromImage(qimg)
        if pm.isNull(): return
        self._last_pix = pm
        self._update_pixmap()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(e)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self):
        if not self._last_pix:
            return
        target = self.video_lbl.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled = self._last_pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.video_lbl.setPixmap(scaled)


class FullscreenVideo(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("RTSP — Fullscreen")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        # This simple style is fine to keep here
        self.setStyleSheet("background:black;")

        self.video_lbl = QLabel(self)
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video_lbl)

        self._cursor_hidden = False

    def _target_size(self) -> QSize:
        return self.size()

    def on_frame(self, qimg: QImage):
        if not self.isVisible() or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        if pm.isNull():
            return
        scaled = pm.scaled(
            self._target_size(), 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
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


# ============================================================================
# Media Manager Dialog
# ============================================================================

class MediaManagerDialog(QDialog):
    """Dialog for managing and post-processing recorded videos and screenshots."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Media Manager")
        self.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # Instructions
        info = QLabel(
            "<b>Media Post-Processing</b><br>"
            "Select a video or image file to trim, rename, or add text overlays."
        )
        layout.addWidget(info)
        
        # File selection
        file_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(QLabel("File:"))
        file_layout.addWidget(self.file_path_edit, 1)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)
        
        # Operations section
        ops_label = QLabel("<b>Operations:</b>")
        layout.addWidget(ops_label)
        
        # Rename
        rename_layout = QHBoxLayout()
        self.new_name_edit = QLineEdit()
        self.new_name_edit.setPlaceholderText("Enter new filename (without extension)")
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self.rename_file)
        rename_layout.addWidget(QLabel("New Name:"))
        rename_layout.addWidget(self.new_name_edit, 1)
        rename_layout.addWidget(rename_btn)
        layout.addLayout(rename_layout)
        
        # Text overlay
        overlay_layout = QVBoxLayout()
        overlay_layout.addWidget(QLabel("Add Text Overlay:"))
        self.overlay_text_edit = QTextEdit()
        self.overlay_text_edit.setMaximumHeight(60)
        self.overlay_text_edit.setPlaceholderText("Enter text to overlay")
        overlay_layout.addWidget(self.overlay_text_edit)
        
        overlay_controls = QHBoxLayout()
        overlay_controls.addWidget(QLabel("Position:"))
        self.overlay_position_combo = QComboBox()
        self.overlay_position_combo.addItems(["top-left", "top-right", "bottom-left", "bottom-right"])
        overlay_controls.addWidget(self.overlay_position_combo)
        overlay_btn = QPushButton("Apply Overlay")
        overlay_btn.clicked.connect(self.apply_overlay)
        overlay_controls.addWidget(overlay_btn)
        overlay_controls.addStretch()
        overlay_layout.addLayout(overlay_controls)
        layout.addLayout(overlay_layout)
        
        # Video trimming (only for videos)
        trim_layout = QVBoxLayout()
        trim_layout.addWidget(QLabel("Trim Video (seconds):"))
        trim_controls = QHBoxLayout()
        trim_controls.addWidget(QLabel("Start:"))
        self.trim_start_spin = QSpinBox()
        self.trim_start_spin.setRange(0, 3600)
        self.trim_start_spin.setSuffix(" s")
        trim_controls.addWidget(self.trim_start_spin)
        trim_controls.addWidget(QLabel("End:"))
        self.trim_end_spin = QSpinBox()
        self.trim_end_spin.setRange(0, 3600)
        self.trim_end_spin.setSuffix(" s")
        trim_controls.addWidget(self.trim_end_spin)
        trim_btn = QPushButton("Trim Video")
        trim_btn.clicked.connect(self.trim_video)
        trim_controls.addWidget(trim_btn)
        trim_controls.addStretch()
        trim_layout.addLayout(trim_controls)
        layout.addLayout(trim_layout)
        
        # Status
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        
        layout.addStretch()
    
    def browse_file(self):
        """Browse for a media file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Media File", "",
            "Media Files (*.mp4 *.avi *.mkv *.jpg *.jpeg *.png);;All Files (*)"
        )
        if path:
            self.file_path_edit.setText(path)
            self.status_label.setText(f"Selected: {Path(path).name}")
    
    def rename_file(self):
        """Rename the selected file."""
        file_path = self.file_path_edit.text()
        new_name = self.new_name_edit.text().strip()
        
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "Error", "Please select a valid file first.")
            return
        
        if not new_name:
            QMessageBox.warning(self, "Error", "Please enter a new name.")
            return
        
        try:
            old_path = Path(file_path)
            new_path = old_path.parent / f"{new_name}{old_path.suffix}"
            
            if new_path.exists():
                QMessageBox.warning(self, "Error", "A file with that name already exists.")
                return
            
            old_path.rename(new_path)
            self.file_path_edit.setText(str(new_path))
            self.status_label.setText(f"Renamed to: {new_path.name}")
            QMessageBox.information(self, "Success", f"File renamed to {new_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename file: {e}")
    
    def apply_overlay(self):
        """Apply text overlay to an image."""
        file_path = self.file_path_edit.text()
        overlay_text = self.overlay_text_edit.toPlainText().strip()
        position = self.overlay_position_combo.currentText()
        
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "Error", "Please select a valid file first.")
            return
        
        if not overlay_text:
            QMessageBox.warning(self, "Error", "Please enter text to overlay.")
            return
        
        try:
            path = Path(file_path)
            
            # Only works on images
            if path.suffix.lower() not in ['.jpg', '.jpeg', '.png']:
                QMessageBox.warning(self, "Error", "Overlay is only supported for image files.")
                return
            
            # Load image as QImage
            qimg = QImage(str(path))
            if qimg.isNull():
                raise ValueError("Failed to load image")
            
            # Apply overlay
            qimg_with_overlay = overlay_text_on_qimage(qimg, overlay_text, position)
            
            # Save with a new name
            output_path = path.parent / f"{path.stem}_overlay{path.suffix}"
            counter = 1
            while output_path.exists():
                output_path = path.parent / f"{path.stem}_overlay_{counter}{path.suffix}"
                counter += 1
            
            if not qimg_with_overlay.save(str(output_path)):
                raise ValueError("Failed to save image")
            
            self.status_label.setText(f"Saved with overlay: {output_path.name}")
            QMessageBox.information(self, "Success", f"Overlay applied and saved to {output_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply overlay: {e}")
    
    def trim_video(self):
        """Trim a video file using FFmpeg."""
        file_path = self.file_path_edit.text()
        start_time = self.trim_start_spin.value()
        end_time = self.trim_end_spin.value()
        
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "Error", "Please select a valid file first.")
            return
        
        if start_time >= end_time:
            QMessageBox.warning(self, "Error", "Start time must be less than end time.")
            return
        
        try:
            import subprocess
            path = Path(file_path)
            
            # Only works on videos
            if path.suffix.lower() not in ['.mp4', '.avi', '.mkv']:
                QMessageBox.warning(self, "Error", "Trimming is only supported for video files.")
                return
            
            # Create output filename
            output_path = path.parent / f"{path.stem}_trimmed{path.suffix}"
            counter = 1
            while output_path.exists():
                output_path = path.parent / f"{path.stem}_trimmed_{counter}{path.suffix}"
                counter += 1
            
            # Use FFmpeg to trim
            duration = end_time - start_time
            cmd = [
                'ffmpeg', '-i', str(path),
                '-ss', str(start_time),
                '-t', str(duration),
                '-c', 'copy',
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.status_label.setText(f"Trimmed video saved: {output_path.name}")
                QMessageBox.information(self, "Success", f"Video trimmed and saved to {output_path.name}")
            else:
                raise ValueError(f"FFmpeg error: {result.stderr}")
        except FileNotFoundError:
            QMessageBox.critical(self, "Error", "FFmpeg is not installed or not in PATH.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to trim video: {e}")


# ============================================================================
# Main Application Window (from main_window.py)
# ============================================================================

class RtspApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RTSP Viewer")

        self._scale = self._calculate_scale()
        self._pane_target = QSize(
            max(320, int(PANE_TARGET_W * self._scale)),
            max(180, int(PANE_TARGET_H * self._scale))
        )
        self._controls_width = max(260, int(400 * self._scale))
        self._grid_spacing = max(6, int(10 * self._scale))
        self._main_spacing = max(10, int(16 * self._scale))
        self._section_spacing = max(8, int(18 * self._scale))
        self._form_spacing = max(6, int(10 * self._scale))
        self._layout_margin = max(12, int(20 * self._scale))
        self._footer_height = max(40, int(52 * self._scale))

        init_w, init_h = self._initial_window_dimensions()
        self.resize(init_w, init_h)
        self.setMinimumSize(int(init_w * 0.85), int(init_h * 0.85))

        # Per-panel state
        self.panel_states: List[Dict[str, Any]] = [
            {
                "user": "", "pass": "", "ip": "", "port": 554,
                "slug": DEFAULT_CAMERA_SLUG, "channel": "1", "subtype": "0",
                "transport": DEFAULT_TRANSPORT, "latency": DEFAULT_LATENCY_MS,
                "running": False, "title": f"Feed {i + 1}",
                "name_tag": "",  # Optional name tag to replace numeric identifier
                "overlay_enabled": False,  # Whether to overlay name on video/screenshots
                "recording": False,  # Whether currently recording
            } for i in range(4)
        ]
        self.workers: List[VideoWorker] = [VideoWorker() for _ in range(4)]
        self.recorders: List[VideoRecorder] = [VideoRecorder() for _ in range(4)]
        self.active_index: int = 0

        # UI Initialization
        self._init_ui()
        # Apply the modern, centralized stylesheet
        self._apply_modern_stylesheet()

        # Connect workers to panes and recording
        for i, worker in enumerate(self.workers):
            worker.frame_ready.connect(self._make_frame_handler(i))
            worker.status.connect(self._make_status_updater(i))

        # Fullscreen window (initially hidden)
        self.fullwin = FullscreenVideo()
        self._fullscreen_source_index: Optional[int] = None
        
        # Explicitly initialize the UI for the first panel
        self._sync_ui_from_state()
        self._update_active_styles()

    def _calculate_scale(self) -> float:
        screen = QApplication.primaryScreen()
        if not screen:
            return 1.0
        available = screen.availableGeometry()
        base_width = PANE_TARGET_W * 2 + 420
        base_height = PANE_TARGET_H * 2 + 120
        scale_w = available.width() / base_width if base_width else 1.0
        scale_h = available.height() / base_height if base_height else 1.0
        scale = min(scale_w, scale_h, 1.0)
        return max(scale, 0.4)

    def _initial_window_dimensions(self) -> Tuple[int, int]:
        grid_width = self._pane_target.width() * 2 + self._grid_spacing
        total_width = grid_width + self._controls_width + self._main_spacing + self._layout_margin * 2
        grid_height = self._pane_target.height() * 2 + self._grid_spacing
        total_height = grid_height + self._footer_height + self._layout_margin * 2
        return total_width, total_height

    def _apply_modern_stylesheet(self):
        """Defines and applies the Material/Fluent inspired application stylesheet."""
        controls_label_font = max(9, round(10 * self._scale))
        controls_heading_font = max(controls_label_font + 1, round(12 * self._scale))
        input_radius = max(4, round(6 * self._scale))
        input_padding = max(6, round(8 * self._scale))
        button_radius = max(4, round(6 * self._scale))
        button_pad_v = max(6, round(10 * self._scale))
        button_pad_h = max(10, round(16 * self._scale))
        pane_radius = max(6, round(8 * self._scale))
        pane_title_padding_v = max(4, round(6 * self._scale))
        pane_title_padding_h = max(6, round(10 * self._scale))
        pane_title_radius = max(4, round(6 * self._scale))
        spin_button_width = max(14, round(18 * self._scale))

        stylesheet = f"""
            /* GENERAL */
            RtspApp, FullscreenVideo {{
                background-color: #202124; /* Very dark grey background */
            }}
            QLabel {{
                color: #e8eaed; /* Light grey text */
            }}

            /* CONTROLS WIDGET (LEFT PANE) */
            QWidget#controls_widget {{
                background-color: #2d2e30;
                border-radius: {pane_radius}px;
            }}
            QWidget#controls_widget QLabel {{
                font-size: {controls_label_font}pt;
            }}
            QWidget#controls_widget QLabel b {{
                font-size: {controls_heading_font}pt;
            }}

            /* INPUT WIDGETS */
            QLineEdit, QSpinBox, QComboBox {{
                background-color: #3c3d3f;
                color: #e8eaed;
                border: 1px solid #5f6368;
                border-radius: {input_radius}px;
                padding: {input_padding}px;
                font-size: {controls_label_font}pt;
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border: 1px solid #8ab4f8; /* Google blue for focus */
            }}
            QLineEdit[readOnly="true"] {{
                background-color: #202124;
                color: #9aa0a6;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: {spin_button_width}px;
            }}

            /* --- FIX FOR COMBOBOX DROPDOWN --- */
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: #3c3d3f; /* Dark background for the list */
                color: #e8eaed; /* Light text for items */
                selection-background-color: #8ab4f8; /* Blue for selected item */
                selection-color: #202124; /* Dark text for selected item */
                border: 1px solid #5f6368;
                border-radius: {input_radius}px;
                outline: 0px; /* Remove focus outline */
            }}
            /* --- END FIX --- */

            /* BUTTONS */
            QPushButton {{
                background-color: #5f6368;
                color: #e8eaed;
                border: none;
                border-radius: {button_radius}px;
                padding: {button_pad_v}px {button_pad_h}px;
                font-size: {controls_label_font}pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #70757a;
            }}
            QPushButton:pressed {{
                background-color: #505357;
            }}
            QPushButton:disabled {{
                background-color: #3c3d3f;
                color: #70757a;
            }}

            /* PRIMARY ACTION BUTTONS */
            QPushButton#start_btn, QPushButton#start_all_btn {{
                background-color: #8ab4f8;
                color: #202124;
            }}
            QPushButton#start_btn:hover, QPushButton#start_all_btn:hover {{
                background-color: #a1c3fb;
            }}

            /* DESTRUCTIVE ACTION BUTTONS */
            QPushButton#stop_btn:hover, QPushButton#stop_all_btn:hover {{
                background-color: #f28b82; /* Google red for stop hover */
                color: #202124;
            }}

            /* VIDEO PANE STYLING */
            VideoPane {{
                background-color: #2d2e30;
                border: 2px solid #3c3d3f;
                border-radius: {pane_radius}px;
            }}
            VideoPane[active="true"] {{
                border: 2px solid #8ab4f8;
            }}

            QLabel#pane_title {{
                font-size: {max(8, round(9 * self._scale))}pt;
                font-weight: bold;
                color: #bdc1c6;
                padding: {pane_title_padding_v}px {pane_title_padding_h}px;
                background-color: transparent;
            }}

            VideoPane[active="true"] QLabel#pane_title {{
                color: #202124;
                background-color: #8ab4f8;
                border-top-left-radius: {pane_title_radius}px; /* Match parent radius */
                border-top-right-radius: {pane_title_radius}px;
            }}

            QLabel#video_lbl {{
                background-color: #000000;
                color: #5f6368;
            }}
        """
        self.setStyleSheet(stylesheet)

    def _build_combo(self, items: List[str]) -> QComboBox:
        combo = QComboBox(self)
        combo.setView(QListView())
        combo.addItems(items)
        self._tint_combo_palette(combo)
        return combo

    def _tint_combo_palette(self, combo: QComboBox):
        """Ensure combo boxes remain legible across native styles (notably macOS)."""
        palette = combo.palette()
        dark_bg = QColor("#3c3d3f")
        text_fg = QColor("#e8eaed")
        highlight_bg = QColor("#8ab4f8")
        highlight_fg = QColor("#202124")

        palette.setColor(QPalette.ColorRole.Base, dark_bg)
        palette.setColor(QPalette.ColorRole.Window, dark_bg)
        palette.setColor(QPalette.ColorRole.Button, dark_bg)
        palette.setColor(QPalette.ColorRole.ButtonText, text_fg)
        palette.setColor(QPalette.ColorRole.Text, text_fg)
        palette.setColor(QPalette.ColorRole.Highlight, highlight_bg)
        palette.setColor(QPalette.ColorRole.HighlightedText, highlight_fg)

        combo.setPalette(palette)

        view = combo.view()
        view.setPalette(palette)
        view.setStyleSheet(
            "background-color: #3c3d3f;"
            "color: #e8eaed;"
            "selection-background-color: #8ab4f8;"
            "selection-color: #202124;"
        )

    def _init_ui(self):
        # --- Controls (apply to the currently active panel) ---
        self.title_edit = QLineEdit(self)
        self.name_tag_edit = QLineEdit(self)
        self.name_tag_edit.setPlaceholderText("Optional name tag")
        self.overlay_checkbox = QCheckBox("Overlay name on video/snapshots")
        
        self.user_edit = QLineEdit(self)
        self.pass_edit = QLineEdit(self)
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ip_edit = QLineEdit(self)
        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.slug_edit = QLineEdit(self)
        
        self.channel_combo = self._build_combo([str(i) for i in range(1, 17)])
        self.subtype_combo = self._build_combo(["0", "1", "2"])

        self.transport_combo = self._build_combo(["tcp", "udp"])
        self.latency_spin = QSpinBox(self)
        self.latency_spin.setRange(0, 5000)
        self.latency_spin.setSuffix(" ms")

        # --- Action Buttons ---
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("start_btn")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stop_btn")
        self.snapshot_btn = QPushButton("Snapshot")
        self.fullscreen_btn = QPushButton("Fullscreen")
        
        # Recording buttons
        self.record_btn = QPushButton("Start Recording")
        self.record_btn.setObjectName("record_btn")
        self.stop_record_btn = QPushButton("Stop Recording")
        self.stop_record_btn.setObjectName("stop_record_btn")
        
        self.start_all_btn = QPushButton("Start All")
        self.start_all_btn.setObjectName("start_all_btn")
        self.stop_all_btn = QPushButton("Stop All")
        self.stop_all_btn.setObjectName("stop_all_btn")
        self.save_cfg_btn = QPushButton("Save Config")
        self.load_cfg_btn = QPushButton("Load Config")
        self.media_manager_btn = QPushButton("Media Manager")

        # --- Status and Preview ---
        self.url_preview = QLineEdit(self)
        self.url_preview.setReadOnly(True)
        self.status_lbl = QLabel("Idle")

        # --- 2x2 Grid of Video Panes ---
        self.panes: List[VideoPane] = [
            VideoPane(i, target_size=self._pane_target, scale=self._scale) for i in range(4)
        ]
        grid = QGridLayout()
        grid.setSpacing(self._grid_spacing)
        grid.addWidget(self.panes[0], 0, 0)
        grid.addWidget(self.panes[1], 0, 1)
        grid.addWidget(self.panes[2], 1, 0)
        grid.addWidget(self.panes[3], 1, 1)

        # --- Layout Section ---
        controls_widget = QWidget()
        controls_widget.setObjectName("controls_widget")
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setSpacing(self._section_spacing)
        controls_layout.setContentsMargins(
            self._section_spacing,
            self._section_spacing,
            self._section_spacing,
            self._section_spacing,
        )
        controls_widget.setMinimumWidth(self._controls_width)
        controls_widget.setMaximumWidth(self._controls_width)

        form = QFormLayout()
        form.setSpacing(self._form_spacing)
        form.addRow("Title:", self.title_edit)
        form.addRow("Name Tag:", self.name_tag_edit)
        form.addRow("", self.overlay_checkbox)
        form.addRow("Username:", self.user_edit)
        form.addRow("Password:", self.pass_edit)
        form.addRow("IP/Host:", self.ip_edit)
        form.addRow("Port:", self.port_spin)
        form.addRow("Path/Slug:", self.slug_edit)
        form.addRow("Channel:", self.channel_combo)
        form.addRow("Subtype:", self.subtype_combo)
        form.addRow("Transport:", self.transport_combo)
        form.addRow("Latency:", self.latency_spin)

        # Button row for individual stream actions
        single_stream_actions = QHBoxLayout()
        single_stream_actions.setSpacing(self._form_spacing)
        single_stream_actions.addWidget(self.start_btn)
        single_stream_actions.addWidget(self.stop_btn)
        
        # Row for recording actions
        recording_actions = QHBoxLayout()
        recording_actions.setSpacing(self._form_spacing)
        recording_actions.addWidget(self.record_btn)
        recording_actions.addWidget(self.stop_record_btn)
        
        # Row for view/capture actions
        view_actions = QHBoxLayout()
        view_actions.setSpacing(self._form_spacing)
        view_actions.addWidget(self.snapshot_btn)
        view_actions.addWidget(self.fullscreen_btn)

        # Row for global stream controls
        global_stream_actions = QHBoxLayout()
        global_stream_actions.setSpacing(self._form_spacing)
        global_stream_actions.addWidget(self.start_all_btn)
        global_stream_actions.addWidget(self.stop_all_btn)
        global_stream_actions.addStretch(1) # Push buttons to the left

        # Row for configuration controls
        config_actions = QHBoxLayout()
        config_actions.setSpacing(self._form_spacing)
        config_actions.addWidget(self.save_cfg_btn)
        config_actions.addWidget(self.load_cfg_btn)
        config_actions.addStretch(1) # Push buttons to the left
        
        # Row for media management
        media_actions = QHBoxLayout()
        media_actions.setSpacing(self._form_spacing)
        media_actions.addWidget(self.media_manager_btn)
        media_actions.addStretch(1)

        # Assemble the controls in the left-side vertical layout
        controls_layout.addWidget(QLabel("<b>Active Panel Controls</b>"))
        controls_layout.addLayout(form)
        controls_layout.addSpacing(max(6, int(self._section_spacing * 0.6)))
        controls_layout.addWidget(QLabel("RTSP URL Preview:"))
        controls_layout.addWidget(self.url_preview)
        controls_layout.addLayout(single_stream_actions)
        controls_layout.addLayout(recording_actions)
        controls_layout.addLayout(view_actions)
        controls_layout.addSpacing(max(12, int(self._section_spacing * 1.1))) # Add a visual separator

        # Add a title for the global actions section
        controls_layout.addWidget(QLabel("<b>Global Actions</b>"))
        controls_layout.addLayout(global_stream_actions)
        controls_layout.addLayout(config_actions)
        controls_layout.addLayout(media_actions)
        controls_layout.addStretch(1)

        main_layout = QHBoxLayout()
        main_layout.setSpacing(self._main_spacing)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(controls_widget)
        main_layout.addLayout(grid, 1)

        top_level_layout = QVBoxLayout(self)
        top_level_layout.setContentsMargins(
            self._layout_margin,
            self._layout_margin,
            self._layout_margin,
            self._layout_margin,
        )
        top_level_layout.setSpacing(self._section_spacing)
        top_level_layout.addLayout(main_layout)
        top_level_layout.addWidget(self.status_lbl)

        # --- Connect Signals to Slots ---
        for p in self.panes:
            p.clicked.connect(self.set_active_panel)

        for w in (self.title_edit, self.name_tag_edit, self.user_edit, self.pass_edit, self.ip_edit, self.slug_edit):
            w.textChanged.connect(self.update_preview)
        for w in (self.port_spin, self.latency_spin):
            w.valueChanged.connect(self.update_preview)
        self.transport_combo.currentIndexChanged.connect(self.update_preview)
        self.overlay_checkbox.stateChanged.connect(self._handle_overlay_toggle)

        for w in (self.channel_combo, self.subtype_combo):
            w.currentIndexChanged.connect(self._handle_stream_parameter_change)

        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snapshot_btn.clicked.connect(self.snapshot)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.record_btn.clicked.connect(self.start_recording)
        self.stop_record_btn.clicked.connect(self.stop_recording)
        self.start_all_btn.clicked.connect(self.start_all_streams)
        self.stop_all_btn.clicked.connect(self.stop_all_streams)
        self.save_cfg_btn.clicked.connect(self.save_config)
        self.load_cfg_btn.clicked.connect(self.load_config)
        self.media_manager_btn.clicked.connect(self.open_media_manager)

    def build_url_from_state(self, st: Dict[str, Any], include_password: bool = True) -> str:
        user = st.get("user", "")
        pwd = st.get("pass", "")
        ip = st.get("ip", "")
        port = st.get("port", 554)
        slug = st.get("slug", "")
        channel = st.get("channel", "1")
        subtype = st.get("subtype", "0")

        if not ip: return ""
        if not slug.startswith("/"): slug = "/" + slug

        cred = ""
        if user:
            cred_pass = f":{pwd}" if pwd and include_password else ""
            cred = f"{user}{cred_pass}@"

        return f"rtsp://{cred}{ip}:{port}{slug}?channel={channel}&subtype={subtype}"

    def _sync_ui_from_state(self):
        st = self.panel_states[self.active_index]
        self.channel_combo.blockSignals(True)
        self.subtype_combo.blockSignals(True)
        self.transport_combo.blockSignals(True)
        self.overlay_checkbox.blockSignals(True)
        try:
            self.title_edit.setText(st["title"])
            self.name_tag_edit.setText(st.get("name_tag", ""))
            self.overlay_checkbox.setChecked(st.get("overlay_enabled", False))
            self.user_edit.setText(st["user"])
            self.pass_edit.setText(st["pass"])
            self.ip_edit.setText(st["ip"])
            self.port_spin.setValue(st["port"])
            self.slug_edit.setText(st["slug"])
            self._set_combo_value(self.channel_combo, st["channel"])
            self._set_combo_value(self.subtype_combo, st["subtype"])
            self._set_combo_value(self.transport_combo, st["transport"])
            self.latency_spin.setValue(st["latency"])
        finally:
            self.channel_combo.blockSignals(False)
            self.subtype_combo.blockSignals(False)
            self.transport_combo.blockSignals(False)
            self.overlay_checkbox.blockSignals(False)
        self._update_buttons_enabled()
        self.update_preview()

    def _sync_state_from_ui(self):
        st = self.panel_states[self.active_index]
        st["title"] = self.title_edit.text()
        st["name_tag"] = self.name_tag_edit.text()
        st["overlay_enabled"] = self.overlay_checkbox.isChecked()
        st["user"] = self.user_edit.text()
        st["pass"] = self.pass_edit.text()
        st["ip"] = self.ip_edit.text()
        st["port"] = self.port_spin.value()
        st["slug"] = self.slug_edit.text()
        st["channel"] = self.channel_combo.currentText()
        st["subtype"] = self.subtype_combo.currentText()
        st["transport"] = self.transport_combo.currentText()
        st["latency"] = self.latency_spin.value()
        
        # Update pane title with name tag if available
        name_tag = st.get("name_tag", "").strip()
        display_name = name_tag if name_tag else st["title"]
        self.panes[self.active_index].title.setText(display_name)

    def _set_combo_value(self, combo: QComboBox, value: str):
        idx = combo.findText(str(value))
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def update_preview(self):
        temp_state = {
            "user": self.user_edit.text(), "pass": self.pass_edit.text(),
            "ip": self.ip_edit.text(), "port": self.port_spin.value(),
            "slug": self.slug_edit.text(), "channel": self.channel_combo.currentText(),
            "subtype": self.subtype_combo.currentText(),
        }
        url = self.build_url_from_state(temp_state, include_password=False)
        self.url_preview.setText(url)
        self.url_preview.setCursorPosition(0)

    def _handle_stream_parameter_change(self, _=None):
        self.update_preview()
        if self.panel_states[self.active_index].get("running", False):
            self.stop_stream()
            self.start_stream()
    
    def _handle_overlay_toggle(self, state):
        """Handle overlay checkbox toggle."""
        self._sync_state_from_ui()

    def set_active_panel(self, index: int):
        if not (0 <= index < 4) or index == self.active_index: return
        self._sync_state_from_ui()
        self.active_index = index
        self._sync_ui_from_state()
        self._update_active_styles()
        if self.fullwin.isVisible():
            self._connect_fullscreen_to(index)

    def _update_active_styles(self):
        for i, p in enumerate(self.panes):
            p.set_active(i == self.active_index)

    def start_stream(self):
        self._sync_state_from_ui()
        st = self.panel_states[self.active_index]
        url = self.build_url_from_state(st)
        if not url:
            QMessageBox.warning(self, "Missing IP", "Please enter an IP address or hostname.")
            return
        worker = self.workers[self.active_index]
        worker.start(url, st["transport"], st["latency"])
        st["running"] = True
        self._update_buttons_enabled()

    def stop_stream(self):
        self.workers[self.active_index].stop()
        self.panel_states[self.active_index]["running"] = False
        self._update_buttons_enabled()

    def snapshot(self):
        st = self.panel_states[self.active_index]
        if not st["running"]:
            QMessageBox.information(self, "Stream Off", "Cannot take a snapshot, the stream is not running.")
            return
        
        # Use name_tag if available, otherwise use title
        name_tag = st.get("name_tag", "").strip()
        file_base = name_tag if name_tag else st['title'].replace(' ', '_')
        default_name = f"{file_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Snapshot", default_name, "Images (*.jpg *.png)")
        if path:
            # Get the current frame and apply overlay if needed
            worker = self.workers[self.active_index]
            if worker._last_qimage is None:
                QMessageBox.warning(self, "Snapshot Failed", "No frame received yet.")
                return
            
            img = worker._last_qimage
            
            # Apply overlay if enabled
            if st.get("overlay_enabled", False) and name_tag:
                img = overlay_text_on_qimage(img, name_tag, "top-left")
            
            if not img.save(path):
                QMessageBox.warning(self, "Snapshot Failed", "Could not save snapshot.")
    
    def start_recording(self):
        """Start recording the active panel's video."""
        st = self.panel_states[self.active_index]
        if not st["running"]:
            QMessageBox.information(self, "Stream Off", "Cannot record, the stream is not running.")
            return
        
        if st.get("recording", False):
            QMessageBox.information(self, "Already Recording", "This panel is already recording.")
            return
        
        # Use name_tag if available, otherwise use title
        name_tag = st.get("name_tag", "").strip()
        file_base = name_tag if name_tag else st['title'].replace(' ', '_')
        default_name = f"{file_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Recording", default_name, "Video Files (*.mp4)")
        if path:
            # Start the recorder
            recorder = self.recorders[self.active_index]
            if recorder.start_recording(path, PANE_TARGET_W, PANE_TARGET_H):
                st["recording"] = True
                self.panes[self.active_index].set_recording(True)
                self._update_buttons_enabled()
                self.status_lbl.setText(f"Recording started: {Path(path).name}")
            else:
                QMessageBox.warning(self, "Recording Failed", "Could not start recording.")
    
    def stop_recording(self):
        """Stop recording the active panel's video."""
        st = self.panel_states[self.active_index]
        if not st.get("recording", False):
            QMessageBox.information(self, "Not Recording", "This panel is not currently recording.")
            return
        
        recorder = self.recorders[self.active_index]
        output_path = recorder.stop_recording()
        st["recording"] = False
        self.panes[self.active_index].set_recording(False)
        self._update_buttons_enabled()
        
        if output_path:
            self.status_lbl.setText(f"Recording saved: {Path(output_path).name}")
            QMessageBox.information(self, "Recording Saved", f"Recording saved to:\n{output_path}")
        else:
            QMessageBox.warning(self, "Recording Failed", "Recording failed or no frames were recorded.")
    
    def open_media_manager(self):
        """Open the media manager dialog."""
        dialog = MediaManagerDialog(self)
        dialog.exec()

    def _update_buttons_enabled(self):
        st = self.panel_states[self.active_index]
        running = st["running"]
        recording = st.get("recording", False)
        
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.snapshot_btn.setEnabled(running)
        self.fullscreen_btn.setEnabled(True)
        self.record_btn.setEnabled(running and not recording)
        self.stop_record_btn.setEnabled(recording)

    def start_all_streams(self):
        self._sync_state_from_ui()
        for i, st in enumerate(self.panel_states):
            if not st["running"] and st["ip"]:
                url = self.build_url_from_state(st)
                self.workers[i].start(url, st["transport"], st["latency"])
                st["running"] = True
        self._update_buttons_enabled()

    def stop_all_streams(self):
        for i in range(4):
            if self.panel_states[i]["running"]:
                self.workers[i].stop()
                self.panel_states[i]["running"] = False
        self._update_buttons_enabled()

    def toggle_fullscreen(self):
        if self.fullwin.isVisible():
            self.fullwin.hide()
        else:
            self._connect_fullscreen_to(self.active_index)
            self.fullwin.showFullScreen()

    def _connect_fullscreen_to(self, idx: int):
        if self._fullscreen_source_index is not None:
            try: self.workers[self._fullscreen_source_index].frame_ready.disconnect(self.fullwin.on_frame)
            except TypeError: pass
        self.workers[idx].frame_ready.connect(self.fullwin.on_frame)
        self._fullscreen_source_index = idx

    def save_config(self):
        self._sync_state_from_ui()
        path, _ = QFileDialog.getSaveFileName(self, "Save Configuration", "", "JSON Files (*.json)")
        if path:
            data = {"version": 2, "panels": self.panel_states}
            try:
                with open(path, "w") as f: json.dump(data, f, indent=2)
                self.status_lbl.setText(f"Configuration saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error Saving", f"Could not save config file:\n{e}")

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Configuration", "", "JSON Files (*.json)")
        if path:
            try:
                with open(path, "r") as f: data = json.load(f)
                loaded_states = data.get("panels", data)
                if isinstance(loaded_states, list) and len(loaded_states) == 4:
                    self.stop_all_streams()
                    
                    # Ensure backward compatibility with old config files
                    for st in loaded_states:
                        # Add new fields with defaults if they don't exist
                        if "name_tag" not in st:
                            st["name_tag"] = ""
                        if "overlay_enabled" not in st:
                            st["overlay_enabled"] = False
                        if "recording" not in st:
                            st["recording"] = False
                    
                    self.panel_states = loaded_states
                    for st in self.panel_states:
                        st['running'] = False
                    self.active_index = 0
                    self._sync_ui_from_state()
                    self._update_active_styles()
                    self.status_lbl.setText(f"Configuration loaded from {path}")
                else:
                    raise ValueError("Config file must contain a list/key 'panels' of 4 states.")
            except Exception as e:
                QMessageBox.critical(self, "Error Loading", f"Could not load config file:\n{e}")

    def closeEvent(self, e):
        self.stop_all_streams()
        # Stop all recordings
        for i in range(4):
            if self.recorders[i].is_recording():
                self.recorders[i].stop_recording()
        super().closeEvent(e)

    def _make_frame_handler(self, index: int):
        """Create a frame handler that processes frames for display, overlay, and recording."""
        def handle_frame(qimg: QImage):
            if qimg.isNull():
                return
            
            st = self.panel_states[index]
            processed_img = qimg
            
            # Apply name tag overlay if enabled
            if st.get("overlay_enabled", False):
                name_tag = st.get("name_tag", "").strip()
                if name_tag:
                    processed_img = overlay_text_on_qimage(processed_img, name_tag, "top-left")
            
            # Send to display
            self.panes[index].on_frame(processed_img)
            
            # Send to fullscreen if active
            if self._fullscreen_source_index == index and self.fullwin.isVisible():
                self.fullwin.on_frame(processed_img)
            
            # Record frame if recording
            if st.get("recording", False) and self.recorders[index].is_recording():
                self.recorders[index].write_frame(processed_img)
        
        return handle_frame

    def _make_status_updater(self, index: int):
        def update_status(msg: str):
            st = self.panel_states[index]
            # Use name_tag if available, otherwise use title
            name_tag = st.get('name_tag', '').strip()
            display_name = name_tag if name_tag else st.get('title', f'Feed {index+1}')
            self.panes[index].title.setText(f"{display_name}: {msg}")
            if index == self.active_index:
                self.status_lbl.setText(f"Panel {index+1}: {msg}")
        return update_status


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RtspApp()
    window.show()
    sys.exit(app.exec())
