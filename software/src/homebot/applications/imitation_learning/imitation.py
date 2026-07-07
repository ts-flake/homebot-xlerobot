"""Imitation learning app placeholder."""

from homebot.utils.pretty_logging import get_logger

_, logger = get_logger(__name__)


class ImitationLearner:
    def __init__(self):
        # setup recording and playback
        # may subscribe to action commands
        from software.src.homebot.common.zmq_utils import create_socket
        self.socket = create_socket(zmq.SUB, bind=False, address="tcp://localhost:5580")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")

    def record_action(self, data):
        logger.debug("recording action")

    def replay(self):
        logger.debug("replaying recorded actions")

    def record_action(self, data):
        logger.debug("recording action")

    def replay(self):
        logger.debug("replaying recorded actions")
