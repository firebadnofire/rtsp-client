# main_window.py

import json
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QFormLayout, QGridLayout
)

# Import our separated modules
from config import PANE_TARGET_W, PANE_TARGET_H, DEFAULT_CAMERA_SLUG, DEFAULT_TRANSPORT, DEFAULT_LATENCY_MS
from video_worker import VideoWorker
from widgets import VideoPane, FullscreenVideo


class RtspApp(QWidget):
    def __init__(self):
        super().__init__()
        # --- MODIFIED: Adjusted window title and initial size for the new layout ---
        self.setWindowTitle("RTSP Viewer")
        self.resize(PANE_TARGET_W * 2 + 420, PANE_TARGET_H * 2 + 40)

        # Per-panel state
        self.panel_states: List[Dict[str, Any]] = [
            {
                "user": "", "pass": "", "ip": "", "port": 554,
                "slug": DEFAULT_CAMERA_SLUG, "channel": "1", "subtype": "0",
                "transport": DEFAULT_TRANSPORT, "latency": DEFAULT_LATENCY_MS,
                "running": False, "title": f"Feed {i + 1}",
            } for i in range(4)
        ]

        # Video decoding workers
        self.workers: List[VideoWorker] = [VideoWorker() for _ in range(4)]

        # --- UI Initialization ---
        self._init_ui()

        # Connect workers to panes for video frames and status updates
        for i, worker in enumerate(self.workers):
            worker.frame_ready.connect(self.panes[i].on_frame)
            worker.status.connect(self._make_status_updater(i))

        # Fullscreen window (initially hidden)
        self.fullwin = FullscreenVideo()
        self._fullscreen_source_index: Optional[int] = None

        # --- MODIFIED ---
        # Explicitly initialize the UI for the first panel.
        # This makes the startup sequence clear and removes special-case logic
        # from the set_active_panel method, resolving inconsistent state handling.
        self.active_index: int = 0
        self._sync_ui_from_state()
        self._update_active_styles()


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
        
        self.channel_combo = QComboBox(self)
        self.channel_combo.addItems([str(i) for i in range(1, 17)])
        self.subtype_combo = QComboBox(self)
        self.subtype_combo.addItems(["0", "1", "2"])

        self.transport_combo = QComboBox(self)
        self.transport_combo.addItems(["tcp", "udp"])
        self.latency_spin = QSpinBox(self)
        self.latency_spin.setRange(0, 5000)
        self.latency_spin.setSuffix(" ms")

        # --- Action Buttons ---
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.snapshot_btn = QPushButton("Snapshot")
        self.fullscreen_btn = QPushButton("Fullscreen")
        self.start_all_btn = QPushButton("Start All")
        self.stop_all_btn = QPushButton("Stop All")
        self.save_cfg_btn = QPushButton("Save Config")
        self.load_cfg_btn = QPushButton("Load Config")

        # --- Status and Preview ---
        self.url_preview = QLineEdit(self)
        self.url_preview.setReadOnly(True)
        self.status_lbl = QLabel("Idle")

        # --- 2x2 Grid of Video Panes ---
        self.panes: List[VideoPane] = [VideoPane(i) for i in range(4)]
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.addWidget(self.panes[0], 0, 0)
        grid.addWidget(self.panes[1], 0, 1)
        grid.addWidget(self.panes[2], 1, 0)
        grid.addWidget(self.panes[3], 1, 1)

        # --- vvvvvv MODIFIED LAYOUT SECTION vvvvvv ---

        # Left side: A container widget for all controls
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_widget.setFixedWidth(400)
        
        # Form for camera details
        form = QFormLayout()
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
        row2 = QHBoxLayout()
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.snapshot_btn)
        row2.addWidget(self.fullscreen_btn)

        # Button row for global actions
        row3 = QHBoxLayout()
        row3.addWidget(self.start_all_btn)
        row3.addWidget(self.stop_all_btn)
        row3.addStretch(1)
        row3.addWidget(self.save_cfg_btn)
        row3.addWidget(self.load_cfg_btn)

        # Assemble the controls in the left-side vertical layout
        controls_layout.addWidget(QLabel("<b>Active panel</b> (Click a feed to select)"))
        controls_layout.addLayout(form)
        controls_layout.addWidget(QLabel("RTSP URL Preview:"))
        controls_layout.addWidget(self.url_preview)
        controls_layout.addLayout(row2)
        controls_layout.addLayout(row3)
        controls_layout.addStretch(1) # Pushes controls to the top
        
        # Main layout is a horizontal split
        main_layout = QHBoxLayout()
        main_layout.addWidget(controls_widget)
        main_layout.addLayout(grid, 1) # The '1' allows the grid to expand

        # Top-level layout to hold the main split view and the status bar
        top_level_layout = QVBoxLayout(self)
        top_level_layout.addLayout(main_layout)
        top_level_layout.addWidget(self.status_lbl)

        # --- ^^^^^^ END MODIFIED LAYOUT SECTION ^^^^^^ ---

        # --- Connect Signals to Slots ---
        for p in self.panes:
            p.clicked.connect(self.set_active_panel)

        # --- MODIFIED: Split signal connections for auto-restarting ---
        # These controls only update the URL preview, they do not restart the stream
        for w in (self.title_edit, self.user_edit, self.pass_edit, self.ip_edit, self.slug_edit):
            w.textChanged.connect(self.update_preview)
        for w in (self.port_spin, self.latency_spin):
            w.valueChanged.connect(self.update_preview)
        self.transport_combo.currentIndexChanged.connect(self.update_preview)

        # These controls will trigger a stream restart if the stream is already running
        for w in (self.channel_combo, self.subtype_combo):
            w.currentIndexChanged.connect(self._handle_stream_parameter_change)
        # --- END MODIFICATION ---

        self.start_btn.clicked.connect(self.start_stream)
        self.stop_btn.clicked.connect(self.stop_stream)
        self.snapshot_btn.clicked.connect(self.snapshot)
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
        # Create a temporary state from the UI to generate the preview URL
        # without saving potentially incomplete data to the main state.
        temp_state = {
            "user": self.user_edit.text(),
            "pass": self.pass_edit.text(),
            "ip": self.ip_edit.text(),
            "port": self.port_spin.value(),
            "slug": self.slug_edit.text(),
            "channel": self.channel_combo.currentText(),
            "subtype": self.subtype_combo.currentText(),
        }
        url = self.build_url_from_state(temp_state, include_password=False)
        self.url_preview.setText(url)
        self.url_preview.setCursorPosition(0)

    # --- NEW METHOD ---
    def _handle_stream_parameter_change(self, _=None):
        """
        Updates the preview and restarts the active stream if it was running.
        This is connected to controls like channel and subtype dropdowns.
        """
        # Always update the preview URL when a parameter changes.
        self.update_preview()

        # Check if the stream for this panel was already running.
        if self.panel_states[self.active_index].get("running", False):
            # It was running, so stop it...
            self.stop_stream()
            # ...and start it again. start_stream() will sync from the UI,
            # picking up the new channel/subtype value.
            self.start_stream()
    # --- END NEW METHOD ---

    def set_active_panel(self, index: int):
        # --- MODIFIED ---
        # This logic is simplified to be more consistent. We now assume
        # self.active_index always exists because it's set in __init__.
        if not (0 <= index < 4): return
        if index == self.active_index: return

        # First, save the current UI's state to the previously active panel.
        self._sync_state_from_ui()

        # Then, update the index and load the new panel's state into the UI.
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
        default_name = f"{st['title'].replace(' ', '_')}.jpg"
        path, _ = QFileDialog.getSaveFileName(self, "Save Snapshot", default_name, "Images (*.jpg *.png)")
        if path and not self.workers[self.active_index].save_snapshot(path):
            QMessageBox.warning(self, "Snapshot Failed", "Could not save snapshot. No frame received yet?")

    def _update_buttons_enabled(self):
        running = self.panel_states[self.active_index]["running"]
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.snapshot_btn.setEnabled(running)

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
                    for i, st in enumerate(self.panel_states):
                        # Load config but don't auto-start. Let the user start them.
                        st['running'] = False
                    
                    # After loading, refresh the UI for the active panel (panel 0)
                    # MODIFIED: Directly sync the UI instead of relying on the panel switch
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
