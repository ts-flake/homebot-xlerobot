from __future__ import annotations

import abc

from homebot.types import RobotAction

class BaseChassisKinematics(abc.ABC):
    """Chassis kinematics base class.
    
    All subclasses must implement `inverse` and `forward` methods.
    """
    name: str
    
    n_wheels: int
    wheel_radius: float
    wheel_motors: list[str] # name of motors

    @abc.abstractmethod
    def inverse(self, robot_action: RobotAction) -> RobotAction:
        """Base velocity (vx: m/s, vy: m/s, vtheta: rad/s) → wheel speed (rad/s).
        """

    @abc.abstractmethod
    def forward(self, wheel_action: RobotAction) -> RobotAction:
        """wheel speed (rad/s) → base velocity (vx: m/s, vy: m/s, vtheta: rad/s."""
