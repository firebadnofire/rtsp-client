# main.py

import sys
import json
import threading
import time
from typing import Optional, List, Dict, Any, Tuple

import av
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt6.QtGui import QImage, QPixmap, QCursor, QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QFormLayout, QGridLayout, QFrame,
    QSizePolicy, QListView
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
    recording_status = pyqtSignal(bool)  # True when recording, False when stopped

    def __init__(self):
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_qimage: Optional[QImage] = None
        self._recording = False
        self._recording_lock = threading.Lock()
        self._output_container: Optional[Any] = None
        self._output_stream: Optional[Any] = None
        self._recording_path: Optional[str] = None
        self._frame_count = 0
        self._recording_start_pts: Optional[int] = None

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
    
    def start_recording(self, path: str) -> bool:
        """Start recording to MKV file. Returns True if successfully started."""
        with self._recording_lock:
            if self._recording:
                return False
            self._recording_path = path
            self._recording = True
            self.recording_status.emit(True)
            return True
    
    def stop_recording(self):
        """Stop recording and close the output file."""
        with self._recording_lock:
            if not self._recording:
                return
            self._recording = False
            if self._output_container:
                try:
                    self._output_container.close()
                except Exception:
                    pass
                self._output_container = None
                self._output_stream = None
            self.recording_status.emit(False)
    
    def is_recording(self) -> bool:
        """Check if currently recording."""
        with self._recording_lock:
            return self._recording

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
                        
                        # Handle recording if enabled
                        with self._recording_lock:
                            if self._recording and self._output_container is None and self._recording_path:
                                try:
                                    # Initialize output container for MKV recording
                                    self._output_container = av.open(self._recording_path, 'w', format='matroska')
                                    self._output_stream = self._output_container.add_stream('h264', rate=stream.average_rate or 30)
                                    self._output_stream.width = stream.width
                                    self._output_stream.height = stream.height
                                    self._output_stream.pix_fmt = 'yuv420p'
                                    # Use a reasonable bitrate
                                    self._output_stream.bit_rate = 2000000
                                    self._recording_start_pts = frame.pts
                                    self._frame_count = 0
                                except Exception as e:
                                    self.status.emit(f"Recording init failed: {e}")
                                    self._recording = False
                                    self.recording_status.emit(False)
                            
                            # Write frame to output if recording
                            if self._recording and self._output_container and self._output_stream:
                                try:
                                    # Encode and write the frame
                                    new_frame = av.VideoFrame.from_ndarray(
                                        frame.to_ndarray(format='rgb24'), 
                                        format='rgb24'
                                    )
                                    # Use the original frame's PTS relative to recording start
                                    # This preserves the original timing
                                    if frame.pts is not None and self._recording_start_pts is not None:
                                        new_frame.pts = frame.pts - self._recording_start_pts
                                    else:
                                        # Fallback to frame counter if PTS not available
                                        new_frame.pts = self._frame_count
                                    self._frame_count += 1
                                    
                                    for packet in self._output_stream.encode(new_frame):
                                        self._output_container.mux(packet)
                                except Exception as e:
                                    self.status.emit(f"Recording error: {e}")
                        
                        # Convert frame to RGB and keep a copy for display
                        img = frame.to_ndarray(format="rgb24")
                        h, w, _ = img.shape
                        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888)
                        qimg = qimg.copy()
                        self._last_qimage = qimg
                        self.frame_ready.emit(qimg)
                
                # Flush encoder if recording
                with self._recording_lock:
                    if self._recording and self._output_stream:
                        try:
                            for packet in self._output_stream.encode():
                                self._output_container.mux(packet)
                        except Exception:
                            pass
                
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
        
        # Clean up recording on exit
        self.stop_recording()
        self.stopped.emit()


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

        self.title = QLabel(title or f"Feed {index+1}")
        # --- MODIFIED: Added object name for specific styling ---
        self.title.setObjectName("pane_title")

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
        v.addWidget(self.title)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)
        wrap.addWidget(self.video_lbl, 1)
        v.addLayout(wrap, 1)

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
                "running": False, "recording": False, "title": f"Feed {i + 1}",
            } for i in range(4)
        ]
        self.workers: List[VideoWorker] = [VideoWorker() for _ in range(4)]
        self.active_index: int = 0

        # UI Initialization
        self._init_ui()
        # Apply the modern, centralized stylesheet
        self._apply_modern_stylesheet()

        # Connect workers to panes
        for i, worker in enumerate(self.workers):
            worker.frame_ready.connect(self.panes[i].on_frame)
            worker.status.connect(self._make_status_updater(i))
            worker.recording_status.connect(self._make_recording_status_updater(i))

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
            
            /* RECORDING BUTTON - RED WHEN ACTIVE */
            QPushButton#record_btn[recording="true"] {{
                background-color: #ea4335; /* Google red for recording */
                color: #ffffff;
            }}
            QPushButton#record_btn[recording="true"]:hover {{
                background-color: #f28b82;
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
        self.record_btn = QPushButton("Record")
        self.record_btn.setObjectName("record_btn")
        self.fullscreen_btn = QPushButton("Fullscreen")
        self.start_all_btn = QPushButton("Start All")
        self.start_all_btn.setObjectName("start_all_btn")
        self.stop_all_btn = QPushButton("Stop All")
        self.stop_all_btn.setObjectName("stop_all_btn")
        self.save_cfg_btn = QPushButton("Save Config")
        self.load_cfg_btn = QPushButton("Load Config")

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
        single_stream_actions.addWidget(self.snapshot_btn)
        single_stream_actions.addWidget(self.record_btn)
        single_stream_actions.addWidget(self.fullscreen_btn)

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

        # Assemble the controls in the left-side vertical layout
        controls_layout.addWidget(QLabel("<b>Active Panel Controls</b>"))
        controls_layout.addLayout(form)
        controls_layout.addSpacing(max(6, int(self._section_spacing * 0.6)))
        controls_layout.addWidget(QLabel("RTSP URL Preview:"))
        controls_layout.addWidget(self.url_preview)
        controls_layout.addLayout(single_stream_actions)
        controls_layout.addSpacing(max(12, int(self._section_spacing * 1.1))) # Add a visual separator

        # Add a title for the global actions section
        controls_layout.addWidget(QLabel("<b>Global Actions</b>"))
        controls_layout.addLayout(global_stream_actions)
        controls_layout.addLayout(config_actions)
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

        for w in (self.title_edit, self.user_edit, self.pass_edit, self.ip_edit, self.slug_edit):
            w.textChanged.connect(self.update_preview)
        for w in (self.port_spin, self.latency_spin):
            w.valueChanged.connect(self.update_preview)
        self.transport_combo.currentIndexChanged.connect(self.update_preview)

        for w in (self.channel_combo, self.subtype_combo):
            w.currentIndexChanged.connect(self._handle_stream_parameter_change)

        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snapshot_btn.clicked.connect(self.snapshot)
        self.record_btn.clicked.connect(self.toggle_recording)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.start_all_btn.clicked.connect(self.start_all_streams)
        self.stop_all_btn.clicked.connect(self.stop_all_streams)
        self.save_cfg_btn.clicked.connect(self.save_config)
        self.load_cfg_btn.clicked.connect(self.load_config)

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
        try:
            self.title_edit.setText(st["title"])
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
        self._update_buttons_enabled()
        self.update_preview()

    def _sync_state_from_ui(self):
        st = self.panel_states[self.active_index]
        st["title"] = self.title_edit.text()
        st["user"] = self.user_edit.text()
        st["pass"] = self.pass_edit.text()
        st["ip"] = self.ip_edit.text()
        st["port"] = self.port_spin.value()
        st["slug"] = self.slug_edit.text()
        st["channel"] = self.channel_combo.currentText()
        st["subtype"] = self.subtype_combo.currentText()
        st["transport"] = self.transport_combo.currentText()
        st["latency"] = self.latency_spin.value()
        self.panes[self.active_index].title.setText(st["title"])

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
        worker = self.workers[self.active_index]
        # Stop recording if active
        if self.panel_states[self.active_index].get("recording", False):
            worker.stop_recording()
            self.panel_states[self.active_index]["recording"] = False
        worker.stop()
        self.panel_states[self.active_index]["running"] = False
        self._update_buttons_enabled()

    def snapshot(self):
        st = self.panel_states[self.active_index]
        if not st["running"]:
            QMessageBox.information(self, "Stream Off", "Cannot take a snapshot, the stream is not running.")
            return
        default_name = f"{st['title'].replace(' ', '_')}.jpg"
        path, _ = QFileDialog.getSaveFileName(self, "Save Snapshot", default_name, "Images (*.jpg *.png)")
        if path and not self.workers[self.active_index].save_snapshot(path):
            QMessageBox.warning(self, "Snapshot Failed", "Could not save snapshot. No frame received yet?")
    
    def toggle_recording(self):
        """Toggle recording for the active panel."""
        st = self.panel_states[self.active_index]
        worker = self.workers[self.active_index]
        
        if not st["running"]:
            QMessageBox.information(self, "Stream Off", "Cannot record, the stream is not running.")
            return
        
        if st.get("recording", False):
            # Stop recording
            worker.stop_recording()
            st["recording"] = False
            self._update_buttons_enabled()
        else:
            # Start recording
            default_name = f"{st['title'].replace(' ', '_')}.mkv"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Recording", default_name, "Video Files (*.mkv)"
            )
            if path:
                if worker.start_recording(path):
                    st["recording"] = True
                    self._update_buttons_enabled()
                else:
                    QMessageBox.warning(self, "Recording Failed", "Could not start recording.")
    
    def _make_recording_status_updater(self, index: int):
        """Create a callback to update recording status for a specific panel."""
        def update_recording_status(is_recording: bool):
            self.panel_states[index]["recording"] = is_recording
            if index == self.active_index:
                self._update_buttons_enabled()
        return update_recording_status

    def _update_buttons_enabled(self):
        running = self.panel_states[self.active_index]["running"]
        recording = self.panel_states[self.active_index].get("recording", False)
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.snapshot_btn.setEnabled(running)
        self.record_btn.setEnabled(running)
        self.record_btn.setText("Stop Recording" if recording else "Record")
        self.record_btn.setProperty("recording", recording)
        # Re-polish the button to force a style update
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.fullscreen_btn.setEnabled(True)

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
                # Stop recording if active
                if self.panel_states[i].get("recording", False):
                    self.workers[i].stop_recording()
                    self.panel_states[i]["recording"] = False
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
            data = {"version": 1, "panels": self.panel_states}
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
        super().closeEvent(e)

    def _make_status_updater(self, index: int):
        def update_status(msg: str):
            pane_title = self.panel_states[index].get('title', f'Feed {index+1}')
            self.panes[index].title.setText(f"{pane_title}: {msg}")
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
