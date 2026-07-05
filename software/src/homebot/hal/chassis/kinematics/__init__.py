from enum import Enum

from .chassis import BaseChassisKinematics

class ChassisType(Enum):
    OMNI = 'omni'

def make_chassis_kinematics(config: dict) -> BaseChassisKinematics:
    params = dict(config)
    type = ChassisType(params.pop('type'))
    if type is ChassisType.OMNI:
        from .omni_chassis import OmniChassisKinematics

        return OmniChassisKinematics(**params)
    raise ValueError(f"Unsupported kinematics type: {type}")


__all__ = [
    "BaseChassisKinematics",
    "ChassisType",
    "make_chassis_kinematics",
]
