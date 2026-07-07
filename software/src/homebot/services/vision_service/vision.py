from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional
from threading import Lock, Thread

import zmq

from homebot.common.interfaces.msg import Image, CameraStatus
from homebot.utils.zmq_utils import create_socket
from homebot.utils.pretty_logging import get_logger

if TYPE_CHECKING:
    from homebot.configs import CameraConfig

_, logger = get_logger(__name__)


class VisionService:
    """Capture frames and publish them as Image multipart; auto-reinit on camera loss."""

    RECONNECT_AFTER = 30  # consecutive failed captures before reinitializing the camera

    def __init__(
        self,
        config: Optional[CameraConfig] = None,
        *,
        camera_name: str = "head",
        pub_addr: Optional[str] = None,
    ):
        from homebot.configs import get_config, CameraConfig

        cfg = get_config()
        self.camera_name = camera_name
        self.config = (config or cfg.cameras.get(camera_name)
                       or next(iter(cfg.cameras.values()), None) or CameraConfig())
        self.pub_addr = pub_addr or cfg.zmq.vision_pub_addr or "tcp://*:5560"

        self._pub_socket = create_socket(zmq.PUB, bind=True, address=self.pub_addr)
        logger.info("vision PUB bound to %s", self.pub_addr)

        self._device = self.config.path
        self._fps = self.config.fps
        self._width = self.config.width
        self._height = self.config.height
        self._fourcc = self.config.fourcc

        self._cam = None
        self._running = False

    def _init_camera(self) -> bool:
        from homebot.hal.camera.driver import CameraDriver
        try:
            self._cam = CameraDriver(
                self._device, width=self._width, height=self._height,
                fps=self._fps, fourcc=self._fourcc,
            )
            logger.info("camera initialized: %s %dx%d@%d %s",
                        self._device, self._width, self._height, self._fps, self._fourcc)
            return True
        except Exception as e:
            logger.error("camera init failed: %s", e)
            self._cam = None
            return False

    def _reconnect(self) -> None:
        logger.warning("camera reconnecting...")
        if self._cam:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None
        self._init_camera()

    def process_frame(self, frame):
        """Hook for subclasses to run detection/tracking. Default: passthrough."""
        return frame

    def _publish(self, frame_id: int, status: CameraStatus, jpg: bytes = b"",
                 width: int = 0, height: int = 0) -> None:
        img = Image(
            frame_id=frame_id, timestamp_s=time.time(), width=width, height=height,
            encoding="jpeg", status=status, data=jpg,
        )
        try:
            self._pub_socket.send_multipart(img.to_multipart(), flags=zmq.NOBLOCK)
        except zmq.Again:
            pass

    def start(self, display: bool = False) -> None:
        import cv2

        self._init_camera()
        self._running = True
        frame_id = 0
        fails = 0
        interval = 1.0 / self._fps if self._fps > 0 else 0
        logger.info("vision publishing at %d fps", self._fps)

        try:
            while self._running:
                t0 = time.perf_counter()
                frame = self._cam.capture_frame() if self._cam else None

                if frame is None:
                    fails += 1
                    reconnecting = fails >= self.RECONNECT_AFTER
                    self._publish(frame_id, CameraStatus.RECONNECTING if reconnecting else CameraStatus.NO_SIGNAL)
                    if reconnecting:
                        self._reconnect()
                        fails = 0
                    time.sleep(0.1)
                    continue

                fails = 0
                processed = self.process_frame(frame)
                ok, buf = cv2.imencode(".jpg", processed)
                if not ok:
                    logger.warning("encode failed")
                    continue
                h, w = processed.shape[:2]
                self._publish(frame_id, CameraStatus.OK, buf.tobytes(), width=w, height=h)
                frame_id += 1

                if display:
                    cv2.imshow("VisionService", processed)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                rem = interval - (time.perf_counter() - t0)
                if rem > 0:
                    time.sleep(rem)
        except KeyboardInterrupt:
            logger.info("vision interrupted")
        except Exception as e:
            logger.error("vision error: %s", e)
        finally:
            self.stop()
            if display:
                cv2.destroyAllWindows()

    def stop(self) -> None:
        self._running = False
        if self._cam:
            self._cam.release()
            self._cam = None
        logger.info("vision stopped")

    listen = start


class VisionSubscriber:
    """Subscribe to VisionService; keep the latest frame + camera status."""

    def __init__(self, sub_addr: str = "tcp://localhost:5560"):
        self._socket = create_socket(zmq.SUB, bind=False, address=sub_addr)
        # No ZMQ_CONFLATE: it drops multipart. Use low HWM + drain to latest instead.
        self._socket.setsockopt(zmq.RCVHWM, 2)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        self._socket.setsockopt(zmq.RCVTIMEO, 1000)

        self._lock = Lock()
        self._frame = None
        self._frame_id = 0
        self._status = CameraStatus.NO_SIGNAL
        self._running = False
        self._thread = None
        logger.info("vision subscriber connected to %s", sub_addr)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._socket:
            self._socket.close()

    def _loop(self) -> None:
        import cv2
        import numpy as np

        while self._running:
            try:
                parts = self._socket.recv_multipart()
            except zmq.Again:
                continue
            except Exception as e:
                logger.warning("receive error: %s", e)
                continue

            while True:  # drain backlog, keep only the newest
                try:
                    parts = self._socket.recv_multipart(flags=zmq.NOBLOCK)
                except zmq.Again:
                    break

            try:
                img = Image.from_multipart(parts)
            except Exception:
                continue
            frame = None
            if img.data:
                frame = cv2.imdecode(np.frombuffer(img.data, np.uint8), cv2.IMREAD_COLOR)
            with self._lock:
                self._status = img.status
                self._frame_id = img.frame_id
                if frame is not None:
                    self._frame = frame

    @property
    def status(self) -> CameraStatus:
        with self._lock:
            return self._status

    def read_frame(self):
        """Return (frame_id, frame) of the latest frame, or (None, None)."""
        with self._lock:
            if self._frame is None:
                return None, None
            return self._frame_id, self._frame.copy()

    def read_loop(self, callback=None, display: bool = False) -> None:
        import cv2

        if not self._running:
            self.start()
        try:
            while True:
                frame_id, frame = self.read_frame()
                if frame is not None:
                    if callback:
                        callback(frame_id, frame)
                    if display:
                        cv2.imshow("VisionSubscriber", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                else:
                    time.sleep(0.001)
        except KeyboardInterrupt:
            logger.info("vision subscriber interrupted")
        finally:
            if display:
                cv2.destroyAllWindows()
