"""Human-follow application.

Detects a person with a YOLO model and drives the chassis to follow them.

Usage:
    # CLI
    python -m homebot.applications.human_follow

    # Code
    from homebot.applications.human_follow import HumanFollowApp
    HumanFollowApp().run(display=True)
"""

from .detector import HumanDetector, Detection
from .tracker import TargetTracker, Target, TargetStatus
from .controller import FollowController
from .app import HumanFollowApp, FollowMode, FollowStatus, main

__all__ = [
    "HumanDetector",
    "Detection",
    "TargetTracker",
    "Target",
    "TargetStatus",
    "FollowController",
    "HumanFollowApp",
    "FollowMode",
    "FollowStatus",
    "main",
]
