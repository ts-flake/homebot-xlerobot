import os
import sys
import time
import queue
import signal
import subprocess
from threading import Thread, Lock, Event
from typing import Optional, Dict, Any, Generator

import zmq
from flask import Flask, render_template, request, Response
from flask_socketio import SocketIO, emit

from homebot.configs import get_config
from homebot.common.interfaces.msg import Image, SourcePriority
from homebot.services.motion_service.clients import ChassisClient, ArmClient
from homebot.utils.zmq_utils import create_socket
from homebot.utils.pretty_logging import get_logger

logger = get_logger(__name__)

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

SOURCE = SourcePriority.WEB


def _connect_addr(bind_addr: str) -> str:
    """Service bind address (tcp://*:port) -> client connect address."""
    return bind_addr.replace("*", "127.0.0.1")


class VideoStream:
    """Subscribe to VisionService Image frames; keep the raw JPEG for MJPEG relay."""

    def __init__(self, sub_addr: str):
        self.sub_addr = sub_addr
        self._socket: Optional[zmq.Socket] = None
        self._running = False
        self._thread: Optional[Thread] = None
        self._latest_jpeg: Optional[bytes] = None
        self._lock = Lock()

    def start(self) -> bool:
        try:
            self._socket = create_socket(zmq.SUB, bind=False, address=self.sub_addr)
            self._socket.setsockopt(zmq.RCVHWM, 2)
            self._socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._socket.setsockopt(zmq.RCVTIMEO, 1000)
        except Exception as e:
            logger.error("vision connect failed: %s", e)
            return False
        self._running = True
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("video stream subscribed to %s", self.sub_addr)
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._socket:
            self._socket.close()
            self._socket = None

    def _loop(self):
        while self._running:
            try:
                parts = self._socket.recv_multipart()
            except zmq.Again:
                continue
            except Exception as e:
                logger.warning("video receive error: %s", e)
                time.sleep(0.5)
                continue
            # Drain backlog, keep only the newest frame.
            while True:
                try:
                    parts = self._socket.recv_multipart(flags=zmq.NOBLOCK)
                except zmq.Again:
                    break
            try:
                img = Image.from_multipart(parts)
            except Exception:
                continue
            if img.data:
                with self._lock:
                    self._latest_jpeg = img.data

    def get_frame(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    @staticmethod
    def _mjpeg_part(jpeg: bytes) -> bytes:
        return (b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n'
                b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n'
                b'\r\n' + jpeg + b'\r\n')

    @staticmethod
    def _placeholder(text: str) -> Optional[bytes]:
        import cv2
        import numpy as np
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.putText(img, text, (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def generate_mjpeg(self) -> Generator[bytes, None, None]:
        while self._running:
            jpeg = self.get_frame()
            if jpeg:
                yield self._mjpeg_part(jpeg)
                time.sleep(0.01)
            else:
                ph = self._placeholder("No Signal - check vision service")
                if ph:
                    yield self._mjpeg_part(ph)
                time.sleep(0.5)


class ChassisBridge:
    """Stream the latest web velocity to the chassis at a fixed rate.

    One background thread owns the REQ socket; emergency/unlock arrive via a
    queue so all socket use stays on that thread. A client deadman zeroes the
    command when no joystick data arrives within CLIENT_TIMEOUT_MS.
    """

    CLIENT_TIMEOUT_MS = 1000
    SEND_INTERVAL = 0.05   # 20 Hz
    IDLE_INTERVAL = 0.5

    def __init__(self, chassis_addr: str):
        self.client = ChassisClient(chassis_addr)
        self._cmd = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}
        self._cmd_lock = Lock()
        self._last_client_time = 0.0
        self._active = False
        self._emergency_locked = False
        self._last_message = ""
        self._queue: queue.Queue = queue.Queue(maxsize=10)
        self._running = False
        self._thread: Optional[Thread] = None

    def start(self):
        self._running = True
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("chassis bridge started (%s)", self.client.service_addr)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.client.close()

    def update_velocity(self, vx: float, vy: float, vtheta: float):
        with self._cmd_lock:
            self._cmd = {"vx": vx, "vy": vy, "vtheta": vtheta}
            self._last_client_time = time.time()
            self._active = True

    def _enqueue(self, kind: str) -> Dict[str, Any]:
        holder: Dict[str, Any] = {}
        done = Event()
        try:
            self._queue.put_nowait((kind, holder, done))
        except queue.Full:
            return {"success": False, "message": "command queue full"}
        if not done.wait(timeout=2.0):
            return {"success": False, "message": "timeout"}
        return holder.get("result", {"success": False, "message": "no response"})

    def emergency_stop(self) -> Dict[str, Any]:
        return self._enqueue("emergency")

    def unlock(self) -> Dict[str, Any]:
        return self._enqueue("unlock")

    def _handle_queued(self, kind: str) -> Dict[str, Any]:
        if kind == "emergency":
            resp = self.client.send_velocity(0.0, 0.0, 0.0, source=SourcePriority.EMERGENCY)
            if resp and resp.success:
                self._emergency_locked = True
        else:  # unlock
            resp = self.client.unlock()
            if resp and resp.success:
                self._emergency_locked = False
        if resp is None:
            return {"success": False, "message": "no response from chassis"}
        self._last_message = resp.message
        return {"success": resp.success, "message": resp.message}

    def _client_active(self) -> bool:
        if not self._active:
            return False
        if (time.time() - self._last_client_time) * 1000 > self.CLIENT_TIMEOUT_MS:
            self._active = False
            with self._cmd_lock:
                self._cmd = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}
            logger.info("web client idle, pausing chassis stream")
            return False
        return True

    def _loop(self):
        while self._running:
            try:
                kind, holder, done = self._queue.get_nowait()
                holder["result"] = self._handle_queued(kind)
                done.set()
                continue
            except queue.Empty:
                pass

            if not self._client_active():
                time.sleep(self.IDLE_INTERVAL)
                continue

            with self._cmd_lock:
                cmd = dict(self._cmd)
            resp = self.client.send_velocity(cmd["vx"], cmd["vy"], cmd["vtheta"], source=SOURCE)
            if resp is not None:
                self._last_message = resp.message
                if not resp.success and "lock" in resp.message:
                    self._emergency_locked = True
            time.sleep(self.SEND_INTERVAL)

    def get_status(self) -> Dict[str, Any]:
        with self._cmd_lock:
            cmd = dict(self._cmd)
        return {
            "connected": True,
            "current_cmd": cmd,
            "last_message": self._last_message,
            "emergency_locked": self._emergency_locked,
        }


class ArmBridge:
    """Web joystick -> EE deltas for one arm (IK on the service side)."""

    POS_STEP = 0.005   # m per event at full deflection
    MIN_INTERVAL = 0.02

    def __init__(self, arm_addr: str):
        self.client = ArmClient(arm_addr)
        self._lock = Lock()   # REQ socket is used from socketio handler threads
        self._last_sent = 0.0
        self._gripper_closed = False

    def close(self):
        self.client.close()

    def joystick(self, x: float, y: float, axis: str) -> Dict[str, Any]:
        now = time.time()
        if now - self._last_sent < self.MIN_INTERVAL:
            return {"success": True, "throttled": True}
        self._last_sent = now

        # 'base': x -> lateral (y), joystick up (-y) -> raise (z). 'reach': x -> forward (x).
        if axis == "reach":
            ee = {"x": x * self.POS_STEP, "y": 0.0, "z": 0.0}
        else:
            ee = {"x": 0.0, "y": x * self.POS_STEP, "z": -y * self.POS_STEP}

        with self._lock:
            resp = self.client.send_ee_delta(**ee, source=SOURCE)
        if resp is None:
            return {"success": False, "message": "no response from arm"}
        return {"success": resp.success, "message": resp.message}

    def set_gripper(self, closed: bool) -> Dict[str, Any]:
        target = 0.0 if closed else 100.0
        with self._lock:
            resp = self.client.send_joints({"gripper": target}, source=SOURCE)
        if resp and resp.success:
            self._gripper_closed = closed
            return {"success": True, "closed": closed}
        return {"success": False, "closed": self._gripper_closed,
                "message": resp.message if resp else "no response from arm"}

    def home_async(self):
        """Blocking smooth home in a background thread (REP returns after motion)."""
        def _run():
            with self._lock:
                resp = self.client.home(source=SOURCE)
            logger.info("arm home done: %s", resp.message if resp else "no response")
        Thread(target=_run, daemon=True).start()

    @property
    def gripper_closed(self) -> bool:
        return self._gripper_closed


# ── Flask app ─────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    static_folder=os.path.join(os.path.dirname(__file__), 'static'),
)
app.config['SECRET_KEY'] = 'homebot-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

chassis_bridge: Optional[ChassisBridge] = None
arm_bridge: Optional[ArmBridge] = None
video_stream: Optional[VideoStream] = None

human_follow_process: Optional[subprocess.Popen] = None
human_follow_lock = Lock()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    logger.info("video feed request from %s", request.remote_addr)
    if video_stream is None:
        return Response(status=503)
    return Response(
        video_stream.generate_mjpeg(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-cache, no-store, must-revalidate',
                 'Pragma': 'no-cache', 'Expires': '0'},
    )


@socketio.on('connect')
def handle_connect():
    logger.info("web client connected")
    status = chassis_bridge.get_status() if chassis_bridge else {}
    emit('server_response', {
        'status': 'connected',
        'message': 'connected to robot control server',
        'arbiter_connected': status.get('connected', False),
    })


@socketio.on('disconnect')
def handle_disconnect():
    logger.info("web client disconnected")
    if chassis_bridge:
        chassis_bridge.update_velocity(0.0, 0.0, 0.0)


@socketio.on('joystick_data')
def handle_joystick(data):
    """Left joystick -> chassis velocity. Fire-and-forget (no ack)."""
    try:
        left = data.get('left', {})
        left_x = float(left.get('x', 0.0))
        left_y = float(left.get('y', 0.0))

        cfg = get_config().chassis
        vx = -left_y * cfg.max_linear_speed
        vtheta = left_x * cfg.max_angular_speed

        if chassis_bridge:
            chassis_bridge.update_velocity(vx, 0.0, vtheta)
    except Exception as e:
        logger.error("joystick handling failed: %s", e)


@socketio.on('emergency_stop')
def handle_emergency_stop():
    logger.warning("emergency stop requested")
    if not chassis_bridge:
        emit('server_response', {'status': 'emergency_stop', 'error': 'not connected'})
        return
    result = chassis_bridge.emergency_stop()
    emit('server_response', {
        'status': 'emergency_stop',
        'locked': result.get('success', False),
        'message': result.get('message', ''),
    })


@socketio.on('home')
def handle_home():
    """Unlock the chassis and start blocking home on the arm."""
    logger.info("home requested (chassis unlock + arm home)")
    if not chassis_bridge:
        emit('server_response', {'status': 'home', 'error': 'not connected'})
        return
    if arm_bridge:
        arm_bridge.home_async()
    result = chassis_bridge.unlock()
    emit('server_response', {
        'status': 'home',
        'success': result.get('success', False),
        'message': result.get('message', ''),
    })


@socketio.on('get_status')
def handle_get_status():
    if not chassis_bridge:
        return
    status = chassis_bridge.get_status()
    with human_follow_lock:
        status['human_follow_active'] = (human_follow_process is not None
                                         and human_follow_process.poll() is None)
    status['gripper_closed'] = arm_bridge.gripper_closed if arm_bridge else False
    emit('server_status', status)


@socketio.on('arm_joystick')
def handle_arm_joystick(data):
    """Right joystick -> arm EE delta. axis='base': lateral/height; 'reach': forward."""
    if not arm_bridge:
        return
    try:
        x = float(data.get('x', 0))
        y = float(data.get('y', 0))
        axis = data.get('axis', 'base')
        dead_zone = 0.1
        x = 0 if abs(x) < dead_zone else x
        y = 0 if abs(y) < dead_zone else y
        if x == 0 and y == 0:
            return
        arm_bridge.joystick(x, y, axis)
    except Exception as e:
        logger.error("arm joystick handling failed: %s", e)


@socketio.on('gripper_toggle')
def handle_gripper_toggle(data):
    if not arm_bridge:
        emit('server_response', {'status': 'gripper', 'error': 'not connected'})
        return
    result = arm_bridge.set_gripper(bool(data.get('closed', False)))
    emit('server_response', {
        'status': 'gripper',
        'success': result.get('success', False),
        'closed': result.get('closed', False),
        'message': result.get('message', ''),
    })


@socketio.on('toggle_human_follow')
def handle_toggle_human_follow(data):
    global human_follow_process
    requested = bool(data.get('active', False))
    logger.info("human follow request: %s", "start" if requested else "stop")

    with human_follow_lock:
        running = human_follow_process is not None and human_follow_process.poll() is None

        if requested and not running:
            if chassis_bridge and chassis_bridge.get_status().get('emergency_locked'):
                emit('server_response', {'status': 'human_follow', 'success': False,
                                         'active': False, 'message': 'chassis locked; home first'})
                return
            try:
                env = os.environ.copy()
                env['PYTHONPATH'] = _SRC_DIR + os.pathsep + env.get('PYTHONPATH', '')
                cmd = [sys.executable, '-m', 'homebot.applications.human_follow']
                human_follow_process = subprocess.Popen(cmd, cwd=_SRC_DIR, env=env)
                time.sleep(1.0)
                if human_follow_process.poll() is None:
                    logger.info("human follow started (pid %d)", human_follow_process.pid)
                    emit('server_response', {'status': 'human_follow', 'success': True,
                                             'active': True, 'message': 'human follow started'})
                    socketio.emit('follow_status', {'active': True})
                else:
                    code = human_follow_process.returncode
                    human_follow_process = None
                    emit('server_response', {'status': 'human_follow', 'success': False,
                                             'active': False, 'message': f'start failed (rc={code})'})
            except Exception as e:
                logger.error("human follow start failed: %s", e)
                human_follow_process = None
                emit('server_response', {'status': 'human_follow', 'success': False,
                                         'active': False, 'message': str(e)})

        elif not requested and running:
            try:
                human_follow_process.send_signal(signal.SIGTERM)
                try:
                    human_follow_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    human_follow_process.kill()
                    human_follow_process.wait()
                human_follow_process = None
                logger.info("human follow stopped")
                emit('server_response', {'status': 'human_follow', 'success': True,
                                         'active': False, 'message': 'human follow stopped'})
                socketio.emit('follow_status', {'active': False})
            except Exception as e:
                logger.error("human follow stop failed: %s", e)
                emit('server_response', {'status': 'human_follow', 'success': False,
                                         'active': True, 'message': str(e)})
        else:
            emit('server_response', {'status': 'human_follow', 'success': True,
                                     'active': running, 'message': 'no change'})


def run_server(host: str = '0.0.0.0', port: int = 5000, *,
               chassis_addr: Optional[str] = None,
               arm_addr: Optional[str] = None,
               vision_addr: Optional[str] = None,
               arm_name: str = 'left',
               debug: bool = False):
    global chassis_bridge, arm_bridge, video_stream

    cfg = get_config()
    chassis_addr = chassis_addr or _connect_addr(cfg.chassis.service_addr)
    arm_addr = arm_addr or _connect_addr(cfg.arms[arm_name].service_addr)
    vision_addr = vision_addr or _connect_addr(cfg.zmq.vision_pub_addr)

    chassis_bridge = ChassisBridge(chassis_addr)
    chassis_bridge.start()
    arm_bridge = ArmBridge(arm_addr)
    video_stream = VideoStream(vision_addr)
    if not video_stream.start():
        logger.warning("vision unavailable; video feed will show placeholder")

    logger.info("web control server: http://%s:%d (chassis=%s arm=%s vision=%s)",
                host, port, chassis_addr, arm_addr, vision_addr)
    try:
        socketio.run(app, host=host, port=port, debug=debug,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("shutting down...")
    finally:
        global human_follow_process
        with human_follow_lock:
            if human_follow_process is not None and human_follow_process.poll() is None:
                try:
                    human_follow_process.send_signal(signal.SIGTERM)
                    human_follow_process.wait(timeout=2)
                except Exception:
                    human_follow_process.kill()
            human_follow_process = None
        chassis_bridge.stop()
        arm_bridge.close()
        video_stream.stop()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='HomeBot web control')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--chassis', dest='chassis_addr', default=None, help='chassis REP address')
    parser.add_argument('--arm', dest='arm_addr', default=None, help='arm REP address')
    parser.add_argument('--arm-name', default='left', help='which arm (config.arms key)')
    parser.add_argument('--vision', dest='vision_addr', default=None, help='vision PUB address')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    run_server(args.host, args.port,
               chassis_addr=args.chassis_addr, arm_addr=args.arm_addr,
               vision_addr=args.vision_addr, arm_name=args.arm_name,
               debug=args.debug)


if __name__ == '__main__':
    main()
