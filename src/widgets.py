# widgets.py

from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QImage, QPixmap, QCursor, QFont
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame, QApplication

from config import PANE_TARGET_W, PANE_TARGET_H

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

        self.title = QLabel(title or f"Feed {index+1}")
        self.title.setStyleSheet("color:#bbb; background: #222; padding:4px 8px;")
        self.title.setFont(QFont("Monospace", 9))

        self.video_lbl = QLabel()
        self.video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_lbl.setStyleSheet("background:#000; color:#666;")
        self.video_lbl.setText("No video")
        self.video_lbl.setFixedSize(self._target_size)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.title)
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(self.video_lbl)
        wrap.addStretch(1)
        v.addLayout(wrap, 1)

    def sizeHint(self) -> QSize:
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


class FullscreenVideo(QWidget):
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
        return self.size()

    def on_frame(self, qimg: QImage):
        if not self.isVisible() or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        if pm.isNull():
            return
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
