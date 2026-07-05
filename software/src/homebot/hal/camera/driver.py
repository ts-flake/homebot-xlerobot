from homebot.utils.pretty_logging import get_logger

logger = get_logger(__name__)


class CameraDriver:
    def __init__(self, device=0, *, width=None, height=None, fps=None, fourcc="MJPG"):
        """Open the camera. ``device`` is a numeric index or a path (e.g. '/dev/video0').

        fourcc defaults to MJPG (compressed); pass "YUYV" for raw or None to leave as-is.
        FOURCC is set before resolution/fps because V4L2 negotiation depends on the order.
        """
        import cv2
        self._device = device

        # Path devices use the V4L2 backend explicitly for reliable property setting.
        api = cv2.CAP_V4L2 if isinstance(device, str) else cv2.CAP_ANY
        self._cap = cv2.VideoCapture(device, api)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(device)  # fall back to the default backend
        if not self._cap.isOpened():
            raise RuntimeError(f"camera {device} open failed")

        if fourcc:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        if width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if fps:
            self._cap.set(cv2.CAP_PROP_FPS, int(fps))
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # keep only the latest frame
        logger.info("camera %s opened", device)

    def capture_frame(self):
        """Capture a single BGR frame, or None on failure."""
        import cv2
        if self._cap is None:
            raise RuntimeError("camera not initialized")
        try:
            ret, frame = self._cap.read()
        except cv2.error as e:
            # A corrupt/empty MJPG frame makes OpenCV's internal decoder raise
            # (imdecode_ '!buf.empty()'). Treat it as a failed capture, not fatal.
            logger.warning("frame decode error: %s", e)
            return None
        if not ret:
            logger.warning("failed to read frame")
            return None
        return frame

    def release(self):
        if self._cap:
            self._cap.release()
            self._cap = None
            logger.info("camera released")
