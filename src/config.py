# config.py

# Pane size is fixed to keep feeds from "growing" as new frames arrive.
# 960x540 (16:9) gives a 1920x1080 total canvas for the 2x2 grid — a good fit
# while still downscaling cleanly from 4K cameras.
PANE_TARGET_W, PANE_TARGET_H = 960, 540

# Default camera connection parameters can also go here if desired
DEFAULT_CAMERA_SLUG = "/cam/realmonitor"
DEFAULT_TRANSPORT = "tcp"
DEFAULT_LATENCY_MS = 100
