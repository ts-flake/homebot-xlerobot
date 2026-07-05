from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Generic, Optional, TypeVar

from .msg import (
    Command,
    EEDelta,
    JointAngles,
    NavigationVelocity,
    SourcePriority,
    Velocity,
)

TReq = TypeVar("TReq")
TRep = TypeVar("TRep")

_SOURCE_BY_LABEL = {sp.value[0]: sp for sp in SourcePriority}


def _to_source(label) -> SourcePriority:
    return _SOURCE_BY_LABEL.get(label, SourcePriority.UNKNOWN)


@dataclass
class Request(Generic[TReq]):
    source: SourcePriority = SourcePriority.UNKNOWN
    # None -> inherit the source's inherent priority; set an int to force one.
    priority: Optional[int] = None
    command: Command = Command.MOVE
    timestamp_s: Optional[float] = None
    data: Optional[TReq] = None

    @property
    def effective_priority(self) -> int:
        """Forced priority when set, else the source's inherent priority."""
        return self.priority if self.priority is not None else self.source.value[1]


@dataclass
class Response(Generic[TRep]):
    success: bool = True
    message: str = ""
    curr_owner: SourcePriority = SourcePriority.UNKNOWN
    curr_priority: int = 0
    timestamp_s: Optional[float] = None
    data: Optional[TRep] = None


_REGISTRY: dict[str, type["Service"]] = {}


class Service:
    """Base for a request/response service: payload types + JSON wire codec.

    kind is the wire discriminator; req_cls/rep_cls are the .data payload types.
    Subclasses override request()/response() for precise return typing.
    """
    kind: str = ""
    req_cls: Optional[type] = None
    rep_cls: Optional[type] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.kind:
            _REGISTRY[cls.kind] = cls

    @classmethod
    def encode_request(cls, req: Request) -> dict:
        return {
            "kind": cls.kind,
            "source": req.source.value[0],
            "priority": req.priority,
            "command": req.command.value,
            "timestamp_s": req.timestamp_s,
            "data": asdict(req.data) if req.data is not None else None,
        }

    @classmethod
    def decode_request(cls, d: dict) -> Request:
        data = d.get("data")
        return Request(
            source=_to_source(d.get("source")),
            priority=d.get("priority"),
            command=Command(d.get("command", Command.MOVE.value)),
            timestamp_s=d.get("timestamp_s"),
            data=cls.req_cls(**data) if (cls.req_cls and data is not None) else None,
        )

    @classmethod
    def encode_response(cls, rep: Response) -> dict:
        return {
            "kind": cls.kind,
            "success": rep.success,
            "message": rep.message,
            "curr_owner": rep.curr_owner.value[0],
            "curr_priority": rep.curr_priority,
            "timestamp_s": rep.timestamp_s,
            "data": asdict(rep.data) if rep.data is not None else None,
        }

    @classmethod
    def decode_response(cls, d: dict) -> Response:
        data = d.get("data")
        return Response(
            success=d.get("success", False),
            message=d.get("message", ""),
            curr_owner=_to_source(d.get("curr_owner")),
            curr_priority=d.get("curr_priority", 0),
            timestamp_s=d.get("timestamp_s"),
            data=cls.rep_cls(**data) if (cls.rep_cls and data is not None) else None,
        )


def decode_request(d: dict) -> Request:
    """Dispatch a wire dict to its owning service via the 'kind' tag."""
    srv = _REGISTRY.get(d.get("kind"))
    if srv is None:
        raise ValueError(f"Unknown srv kind: {d.get('kind')}")
    return srv.decode_request(d)


class VelocitySrv(Service):
    kind = "velocity"
    req_cls = Velocity
    rep_cls = None

    @staticmethod
    def request() -> Request[Velocity]:
        return Request(data=Velocity())

    @staticmethod
    def response() -> Response[None]:
        return Response()


class NavigationSrv(Service):
    kind = "navigation"
    req_cls = NavigationVelocity
    rep_cls = None

    @staticmethod
    def request() -> Request[NavigationVelocity]:
        return Request(data=NavigationVelocity())

    @staticmethod
    def response() -> Response[None]:
        return Response()


class JointAnglesSrv(Service):
    kind = "joint_angles"
    req_cls = JointAngles
    rep_cls = JointAngles

    @staticmethod
    def request() -> Request[JointAngles]:
        return Request(data=JointAngles())

    @staticmethod
    def response() -> Response[JointAngles]:
        return Response(data=JointAngles())


class EEDeltaSrv(Service):
    kind = "ee_delta"
    req_cls = EEDelta
    rep_cls = JointAngles

    @staticmethod
    def request() -> Request[EEDelta]:
        return Request(data=EEDelta())

    @staticmethod
    def response() -> Response[JointAngles]:
        return Response(data=JointAngles())
