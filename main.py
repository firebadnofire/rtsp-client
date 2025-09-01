# main.py

import sys
import os
from PyQt6.QtWidgets import QApplication

# Add the 'src' directory to the system path. This allows the script to find
# and import the other Python files (like main_window, widgets, etc.)
# from that subdirectory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from main_window import RtspApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RtspApp()
    window.show()
    sys.exit(app.exec())
