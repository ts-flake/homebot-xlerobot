import sys
import argparse
import time
import threading

from homebot.configs import get_config
from homebot.services.motion_service.motor_bus_manager import MotorBusManager
from homebot.hal.chassis import make_wheel_motors_dict, load_chassis_calibration
from homebot.hal.arm import make_arm_motors_dict, load_arm_calibration
from homebot.hal.head import make_head_motors_dict, load_head_calibration
from homebot.utils.pretty_logging import get_logger, init_logging

console_level, logger = get_logger(__name__)

# --service value -> subsystems to start. 'arms' = every arm in config.arms.
_SERVICE_SUBSYSTEMS = {
    'chassis': {'chassis'},
    'arms': {'arms'},
    'head': {'head'},
    'all': {'chassis', 'arms', 'head'},
}


def _subsystems(service: str) -> set:
    return _SERVICE_SUBSYSTEMS[service]


def _build_buses_by_port(config, service: str):
    """Group the selected subsystems' motors by port -> {port: (baudrate, motors, calibration)}.

    Each physical bus is one port carrying several subsystems' motors
    (ACM0 = left_arm + head; ACM1 = right_arm + base). Only motors used this run
    are placed on the bus, otherwise connect() pings absent motors and fails.
    """
    subs = _subsystems(service)
    by_port: dict = {}

    def _slot(port, baudrate):
        if port not in by_port:
            by_port[port] = [baudrate, {}, {}]
        return by_port[port]

    if 'chassis' in subs:
        _, motors, calibration = _slot(config.chassis.port, config.chassis.baudrate)
        motors.update(make_wheel_motors_dict(
            config.chassis.wheel_motors,
            model=config.chassis.wheel_motor_model,
        ))
        calibration.update(load_chassis_calibration(config.chassis.calibration_path))
    if 'arms' in subs:
        for arm in config.arms.values():
            _, motors, calibration = _slot(arm.port, arm.baudrate)
            motors.update(make_arm_motors_dict(
                arm.joint_motors,
                gripper_joint=arm.gripper_joint,
                use_degrees=arm.use_degrees,
                model=arm.joint_motor_model,
            ))
            calibration.update(load_arm_calibration(arm.calibration_path))
    if 'head' in subs:
        _, motors, calibration = _slot(config.head.port, config.head.baudrate)
        motors.update(make_head_motors_dict(
            config.head.joint_motors,
            use_degrees=config.head.use_degrees,
            model=config.head.joint_motor_model,
        ))
        calibration.update(load_head_calibration(config.head.calibration_path))

    return {port: tuple(v) for port, v in by_port.items()}


def main():
    parser = argparse.ArgumentParser(description='HomeBot motion service launcher')
    parser.add_argument('--service', choices=['chassis', 'arms', 'head', 'all'], default='all',
                        help='which service to start (arms = all arms; all = chassis+arms+head)')
    parser.add_argument('--chassis-addr', default=None, help='chassis REP address override')
    parser.add_argument('--head-addr', default=None, help='head REP address override')
    args = parser.parse_args()

    config = get_config()
    
    init_logging(console_level)

    logger.info("chassis port: %s", config.chassis.port)
    for name, arm in config.arms.items():
        logger.info("%s arm port: %s", name, arm.port)
    logger.info("head port: %s", config.head.port)

    buses = _build_buses_by_port(config, args.service)

    bus_manager = MotorBusManager()
    for port, (baudrate, motors, calibration) in buses.items():
        if not bus_manager.initialize_bus(port, baudrate, motors, calibration):
            logger.error("bus init failed: %s, exiting", port)
            bus_manager.close()
            sys.exit(1)
    logger.info("serial buses initialized (%d)", len(buses))

    subs = _subsystems(args.service)
    services = {}
    threads = []

    def _spawn(name, service):
        services[name] = service
        thread = threading.Thread(target=service.start, daemon=False)
        threads.append((name, thread))
        thread.start()

    if 'chassis' in subs:
        from homebot.services.motion_service.chassis import ChassisService
        chassis_addr = args.chassis_addr or config.chassis.service_addr
        _spawn('chassis', ChassisService(config=config.chassis, rep_addr=chassis_addr))
        logger.info("chassis service started (%s)", chassis_addr)

    if 'arms' in subs:
        from homebot.services.motion_service.arm import ArmService
        for name, arm in config.arms.items():
            _spawn(f'arm:{name}', ArmService(config=arm, arm_name=name, rep_addr=arm.service_addr))
            logger.info("%s arm service started (%s)", name, arm.service_addr)

    if 'head' in subs:
        from homebot.services.motion_service.head import HeadService
        head_addr = args.head_addr or config.head.service_addr
        _spawn('head', HeadService(config=config.head, rep_addr=head_addr))
        logger.info("head service started (%s)", head_addr)

    logger.info("Ctrl+C to stop all services")

    try:
        while True:
            time.sleep(0.1)
            for name, thread in threads:
                if not thread.is_alive():
                    logger.warning("%s service thread exited", name)
                    if len(threads) == 1:
                        return
    except KeyboardInterrupt:
        logger.info("stop signal received, shutting down...")
    finally:
        for name, service in services.items():
            try:
                service.stop()
            except Exception as e:
                logger.warning("error stopping %s: %s", name, e)
        for name, thread in threads:
            if thread.is_alive():
                thread.join(timeout=2.0)
                if thread.is_alive():
                    logger.warning("%s thread did not finish in 2s", name)
        bus_manager.close()
        time.sleep(0.1)
        logger.info("all services stopped")


if __name__ == '__main__':
    main()
