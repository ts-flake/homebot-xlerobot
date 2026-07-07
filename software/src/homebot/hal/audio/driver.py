"""Audio driver interface"""

from homebot.utils.pretty_logging import get_logger

_, logger = get_logger(__name__)


class AudioDriver:
    def __init__(self):
        # initialize microphone/speaker
        pass

    def record(self, duration: float):
        logger.debug(f"recording {duration}s of audio")
        return b""

    def play(self, data: bytes):
        logger.debug("playing audio data")
