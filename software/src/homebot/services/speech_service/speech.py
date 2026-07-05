"""SpeechService interface skeleton."""

from homebot.utils.pretty_logging import get_logger

logger = get_logger(__name__)


class SpeechService:
    def __init__(self, rep_addr: str = "tcp://*:5570"):
        # setup audio input/output, speech models
        from software.src.homebot.common.zmq_utils import create_socket
        self.socket = create_socket(zmq.REP, bind=True, address=rep_addr)

    def recognize(self, audio_data) -> str:
        logger.debug("recognizing speech")
        return ""

    def synthesize(self, text: str):
        logger.debug(f"synthesizing text: {text}")

    def serve_forever(self):
        logger.info("SpeechService starting")
        while True:
            msg = self.socket.recv_json()
            logger.debug(f"speech received: {msg}")
            # simple echo
            self.socket.send_json({"status": "ok", "echo": msg})

    def recognize(self, audio_data) -> str:
        logger.debug("recognizing speech")
        return ""

    def synthesize(self, text: str):
        logger.debug(f"synthesizing text: {text}")
