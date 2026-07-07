"""Human-follow application: detection + tracking + control + chassis I/O.

Subscribes to the vision service frame stream, detects and tracks a person,
computes a follow velocity, and drives the chassis service.
"""
import os
import time
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from enum import Enum

import numpy as np

from homebot.configs import get_config, HumanFollowConfig
from homebot.common.interfaces.msg import SourcePriority, Velocity
from homebot.services.vision_service import VisionSubscriber
from homebot.services.motion_service.clients import ChassisClient
from homebot.utils.pretty_logging import get_logger

from .detector import HumanDetector
from .tracker import TargetTracker
from .controller import FollowController

_, logger = get_logger(__name__)

SOURCE = SourcePriority.AUTO


class FollowMode(Enum):
    IDLE = "idle"
    FOLLOWING = "following"
    SEARCHING = "searching"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class FollowStatus:
    mode: FollowMode
    target_id: Optional[int]
    target_confidence: float
    velocity: Velocity
    fps: float
    error_message: Optional[str] = None


def _resolve_model_path(model_path: str) -> str:
    """Absolutize a config model path against the ``software/`` repo root."""
    if os.path.isabs(model_path):
        return model_path
    # app.py lives at software/src/homebot/applications/human_follow/
    software_root = Path(__file__).resolve().parents[4]
    return str(software_root / model_path)


class HumanFollowApp:
    """Vision-driven person follower.

    1. Subscribe to the vision service image stream.
    2. Detect and track a person.
    3. Compute a follow velocity.
    4. Send velocity commands to the chassis service.
    """

    def __init__(self, config: Optional[HumanFollowConfig] = None):
        self.config = config or get_config().human_follow

        self.detector: Optional[HumanDetector] = None
        self.tracker: Optional[TargetTracker] = None
        self.controller: Optional[FollowController] = None
        self.vision_sub: Optional[VisionSubscriber] = None
        self.chassis_client: Optional[ChassisClient] = None

        self.mode = FollowMode.IDLE
        self.running = False
        self._stop_event = threading.Event()

        # FPS stats
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.current_fps = 0.0

        # Current (smoothed) velocity
        self.current_velocity = Velocity()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Bring up vision, detector, tracker, controller and chassis client."""
        logger.info("initializing human-follow: vision=%s chassis=%s model=%s",
                    self.config.vision_sub_addr, self.config.chassis_service_addr,
                    self.config.model_path)
        try:
            # 1. Vision subscriber (starts its own background thread).
            self.vision_sub = VisionSubscriber(self.config.vision_sub_addr)
            self.vision_sub.start()

            # 2. Detector.
            model_path = _resolve_model_path(self.config.model_path)
            if not os.path.exists(model_path):
                logger.error("model file not found: %s", model_path)
                return False
            self.detector = HumanDetector(
                model_path=model_path,
                conf_threshold=self.config.conf_threshold,
                inference_size=self.config.inference_size,
                use_half=self.config.use_half_precision,
            )
            if not self.detector.initialize():
                logger.error("detector init failed")
                return False

            # 3. Tracker.
            self.tracker = TargetTracker(
                max_age=self.config.max_tracking_age,
                min_iou=self.config.min_iou_threshold,
                selection_strategy=self.config.target_selection,
            )

            # 4. Controller (uses the head camera resolution, else the first camera).
            cams = get_config().cameras
            cam_config = cams.get("head") or next(iter(cams.values()))
            self.controller = FollowController(
                target_distance=self.config.target_distance,
                target_width_ratio=self.config.target_width_ratio,
                target_height_ratio=self.config.target_height_ratio,
                kp_linear=self.config.kp_linear,
                kp_angular=self.config.kp_angular,
                max_linear_speed=self.config.max_linear_speed,
                max_angular_speed=self.config.max_angular_speed,
                dead_zone_x=self.config.dead_zone_x,
                dead_zone_area=self.config.dead_zone_area,
                frame_width=cam_config.width,
                frame_height=cam_config.height,
            )

            # 5. Chassis client.
            self.chassis_client = ChassisClient(self.config.chassis_service_addr)

            logger.info("human-follow ready")
            return True

        except Exception as e:
            logger.error("initialization failed: %s", e)
            self.mode = FollowMode.ERROR
            return False

    # ── Helpers ───────────────────────────────────────────────────────

    def _update_fps(self):
        self.frame_count += 1
        elapsed = time.time() - self.last_fps_time
        if elapsed >= 1.0:
            self.current_fps = self.frame_count / elapsed
            self.frame_count = 0
            self.last_fps_time = time.time()

    def _send_velocity(self, velocity: Velocity) -> bool:
        """Send a velocity command to the chassis. Returns True on success."""
        if self.chassis_client is None:
            return False
        try:
            resp = self.chassis_client.send_velocity(
                vx=velocity.linear["x"], vy=velocity.linear["y"],
                vtheta=velocity.angular["z"], source=SOURCE,
            )
            return resp.success if resp else False
        except Exception as e:
            logger.warning("send velocity failed: %s", e)
            return False

    # ── Frame processing / state machine ──────────────────────────────

    def _process_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Detect, track and act on one frame; return a debug visualization."""
        if frame is None:
            return None

        detections = self.detector.detect(frame)
        target = self.tracker.update(detections)
        h, w = frame.shape[:2]

        if target:
            if self.mode in (FollowMode.FOLLOWING, FollowMode.SEARCHING, FollowMode.IDLE):
                # (Re)acquire the target and resume following.
                if self.mode in (FollowMode.SEARCHING, FollowMode.IDLE):
                    logger.info("target acquired, following"
                                if self.mode == FollowMode.IDLE else
                                "target reacquired, resuming")
                    self.mode = FollowMode.FOLLOWING
                    if self.controller:
                        self.controller.target_lost_count = 0

                if self.controller:
                    cmd = self.controller.compute_velocity(target, frame_width=w, frame_height=h)
                    if cmd:
                        self.current_velocity = self.controller.smooth_velocity(
                            self.current_velocity, cmd, alpha=0.3,
                        )
                        self._send_velocity(self.current_velocity)

            elif self.mode == FollowMode.PAUSED:
                self._send_velocity(Velocity())
            # ERROR and other modes: send nothing.

        else:
            # Target lost.
            self.controller.compute_velocity(None)

            if self.mode == FollowMode.FOLLOWING:
                if self.controller.is_searching() and self.config.search_on_lost:
                    self.mode = FollowMode.SEARCHING
                    logger.info("target lost, searching")

                elif self.controller.is_target_lost() or self.config.stop_on_lost:
                    self.current_velocity = self.controller.smooth_velocity(
                        self.current_velocity, Velocity(), alpha=0.5,
                    )
                    self._send_velocity(self.current_velocity)
                    if self.controller.is_target_lost():
                        logger.info("target lost, stopping")
                        self.mode = FollowMode.IDLE

            elif self.mode == FollowMode.SEARCHING:
                if self.controller.is_target_lost():
                    logger.info("search timed out, stopping")
                    self.mode = FollowMode.IDLE
                    self.current_velocity = Velocity()
                    self._send_velocity(self.current_velocity)
                else:
                    search_cmd = self.controller.compute_search_velocity()
                    self.current_velocity = self.controller.smooth_velocity(
                        self.current_velocity, search_cmd, alpha=0.3,
                    )
                    self._send_velocity(self.current_velocity)

        self._update_fps()
        return self._visualize(frame, target, detections)

    def _visualize(self, frame: np.ndarray, target, detections) -> np.ndarray:
        """Draw detections, target and an info panel (debug overlay).

        Drawing uses the actual frame size, decoupled from the control-side
        reference resolution.
        """
        import cv2

        output = frame.copy()
        h, w = output.shape[:2]
        center_x, center_y = w // 2, h // 2

        # All detections (semi-transparent).
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            overlay = output.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 1)
            output = cv2.addWeighted(output, 0.7, overlay, 0.3, 0)

        # Primary target.
        if target:
            x1, y1, x2, y2 = target.bbox
            cx, cy = target.center
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.circle(output, (cx, cy), 5, (0, 255, 255), -1)
            cv2.line(output, (cx, cy), (center_x, cy), (255, 0, 0), 2)
            cv2.line(output, (cx, cy), (cx, center_y), (255, 0, 0), 2)
            label = f"Target {target.id}: {target.confidence:.2f}"
            cv2.putText(output, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        cv2.drawMarker(output, (center_x, center_y), (0, 255, 0),
                       cv2.MARKER_CROSS, 20, 2)

        info_lines = [
            f"Mode: {self.mode.value}",
            f"FPS: {self.current_fps:.1f}",
            f"Velocity: vx={self.current_velocity.linear['x']:+.2f}, vtheta={self.current_velocity.angular['z']:+.2f}",
            f"Targets: {len(self.tracker.targets) if self.tracker else 0}",
        ]
        if target:
            info_lines.append(f"Target ID: {target.id}, Conf: {target.confidence:.2f}")

        panel_height = len(info_lines) * 25 + 10
        cv2.rectangle(output, (5, 5), (350, panel_height), (0, 0, 0), -1)
        cv2.rectangle(output, (5, 5), (350, panel_height), (0, 255, 0), 1)
        for i, line in enumerate(info_lines):
            cv2.putText(output, line, (10, 25 + i * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        return output

    # ── Mode control ──────────────────────────────────────────────────

    def start_following(self) -> bool:
        if self.mode == FollowMode.ERROR:
            logger.error("in error state, cannot start")
            return False
        logger.info("following")
        self.mode = FollowMode.FOLLOWING
        return True

    def stop_following(self):
        logger.info("stop following")
        self.mode = FollowMode.IDLE
        self._send_velocity(Velocity())
        self.current_velocity = Velocity()

    def pause(self):
        logger.info("paused")
        self.mode = FollowMode.PAUSED
        self._send_velocity(Velocity())

    def resume(self):
        logger.info("resumed")
        self.mode = FollowMode.FOLLOWING

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self, display: bool = False):
        if not self.initialize():
            logger.error("initialization failed, aborting")
            return

        self.running = True
        self._stop_event.clear()
        self.start_following()
        logger.info("human-follow started (Ctrl+C to stop)")

        try:
            while self.running and not self._stop_event.is_set():
                frame_id, frame = self.vision_sub.read_frame()
                if frame is None:
                    time.sleep(0.01)
                    continue

                output = self._process_frame(frame)
                if display and output is not None:
                    import cv2
                    cv2.imshow("Human Follow", output)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

        except KeyboardInterrupt:
            logger.info("interrupted")
        except Exception as e:
            logger.error("run error: %s", e)
            self.mode = FollowMode.ERROR
        finally:
            self.stop()
            if display:
                import cv2
                cv2.destroyAllWindows()

    def stop(self):
        logger.info("stopping human-follow")
        self.running = False
        self._stop_event.set()
        self.stop_following()
        if self.vision_sub:
            self.vision_sub.stop()
        if self.detector:
            self.detector.release()
        if self.chassis_client:
            self.chassis_client.close()

    def get_status(self) -> FollowStatus:
        target = self.tracker.get_primary_target() if self.tracker else None
        return FollowStatus(
            mode=self.mode,
            target_id=target.id if target else None,
            target_confidence=target.confidence if target else 0.0,
            velocity=self.current_velocity,
            fps=self.current_fps,
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HomeBot human-follow application")
    parser.add_argument("--display", "-d", action="store_true", help="show debug window")
    args = parser.parse_args()

    app = HumanFollowApp()
    app.run(display=args.display)


if __name__ == "__main__":
    main()
