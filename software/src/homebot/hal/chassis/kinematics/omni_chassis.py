from __future__ import annotations

from dataclasses import dataclass, field
import logging

import numpy as np

from homebot.types import RobotAction
from .chassis import BaseChassisKinematics

logger = logging.getLogger(__name__)


@dataclass
class OmniChassisKinematics(BaseChassisKinematics):
    """Omnidirecion wheel kinemaics.

    Reference: https://hades.mech.northwestern.edu/images/7/7f/MR.pdf#page=531.29

    Top-down topology: forward is x-axis (up), left is y-axis, CCW is positive.

    E.g. omni3-wheel

           (back wheel)
                /\
               /__\
    (right wheel) (left wheel)

    NOTE: assuming motor positive rotation is CW (looking at the wheel).
    """

    name: str = "omni_chassis"
    n_wheels: int = 3 # number of wheels
    wheel_radius: float = 0.05 # meter
    base_radius: float = 0.125 # disance from the wheels to the base center, meter

    # Order of the wheels used in constructing the Jacobian
    wheel_motors: list[str] = field(
        default_factory=lambda: ['left', 'back', 'right']
    )

    # CCW wheel mounting angles starting from x-axis
    wheel_mounting_angles_deg: dict[str, float] = field(
        default_factory=lambda: {'left': 240.0, 'back':0.0, 'right': 120.0}
    )

    def __post_init__(self):
        if len(self.wheel_mounting_angles_deg) != self.n_wheels:
            raise ValueError(
                f"Expects {self.n_wheels} wheel mounting angles, got {len(self.wheel_mounting_angles_deg)}"
            )
        if len(self.wheel_motors) != self.n_wheels:
            raise ValueError(
                f"Expects size of wheel_order to be {self.n_wheels}, got size {len(self.wheel_motors)}"
            )
        
        missing = [name for name in self.wheel_motors if name not in self.wheel_mounting_angles_deg]
        if missing:
            raise KeyError(
                f"Wheel {missing} not found in {list(self.wheel_mounting_angles_deg.keys())}"
            )
        
        # Wheel driving direction = mounting_angle + 90° (CCW)
        drive_angles = np.deg2rad([self.wheel_mounting_angles_deg[name] for name in self.wheel_motors])
        betas = drive_angles + np.pi / 2

        # J: body_twist = [vtheta, vx, vy] → wheel_linear_speed (m/s)
        # wheel_speed_i = base_radius * vtheta + cos(beta_i) * vx + sin(beta_i) * vy
        self._J = np.array(
            [[self.base_radius, np.cos(b), np.sin(b)] for b in betas],
            dtype=float,
        )
        self._J_inv = np.linalg.inv(self._J)

    def inverse(self, robot_action: RobotAction) -> RobotAction:
        vx = robot_action.get('vx', 0.0)
        vy = robot_action.get('vy', 0.0)
        vtheta = robot_action.get('vtheta', 0.0)
        
        body_vel = np.array([vtheta, vx, vy], dtype=float)
        wheel_linear = self._J @ body_vel
        wheel_radps = wheel_linear / self.wheel_radius
        return dict(zip(self.wheel_motors, wheel_radps))

    def forward(self, wheel_action: RobotAction) -> RobotAction:
        if len(wheel_action) != self.n_wheels:
            logger.warning(f"Expected {self.n_wheels} wheel speeds in forward kinematics, got {len(wheel_action)}")
        
        wheel_radps = [wheel_action.get(name, 0.0) for name in self.wheel_motors]
        wheel_linear = np.asarray(wheel_radps, dtype=float) * self.wheel_radius
        vtheta, vx, vy = self._J_inv @ wheel_linear
        return {'vx': vx, 'vy': vy, 'vtheta': vtheta}

if __name__ == '__main__':
    omni3 = OmniChassisKinematics()
    robot_action = {'vx': 0, 'vy': 0, 'vtheta': 1}
    print('IK:')
    wheel_radps = omni3.inverse(robot_action)
    print(f"base vel={robot_action})\nwheel radps={wheel_radps}")
    print()
    print('FK:')
    fk_robot_action = omni3.forward(wheel_radps)
    print(f"true={robot_action}\noutput={fk_robot_action}")