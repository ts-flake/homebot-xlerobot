"""Remote control application logic placeholder."""

from homebot.utils.pretty_logging import get_logger
from common.messages import MessageType, serialize
import zmq

logger = get_logger(__name__)


class RemoteController:
    def __init__(self, pub_addr: str = "tcp://localhost:5555"):
        # setup zmq REQ socket to send commands
        from software.src.homebot.common.zmq_utils import create_socket
        self.socket = create_socket(zmq.REQ, bind=False, address=pub_addr)

    def send_velocity(self, vx: float, vy: float, omega: float):
        # build structured message rather than pre-serialized string
        payload = {
            "type": MessageType.CMD_VELOCITY.value,
            "data": {"vx": vx, "vy": vy, "omega": omega},
        }
        logger.debug(f"sending velocity: {payload}")
        self.socket.send_json(payload)
        reply = self.socket.recv_json()
        logger.debug(f"got reply: {reply}")

