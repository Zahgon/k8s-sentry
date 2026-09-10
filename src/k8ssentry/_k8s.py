"""The Kubernetes API types this tool reads, and nothing more.

``k8s.io/api`` + ``apimachinery`` are hundreds of thousands of generated lines.
Only the fields ``k8s-sentry`` actually touches are modelled here, which keeps
the transformation under test dependency-free. Talking to a real cluster is a
separate concern and lives behind :mod:`k8ssentry.transport`.

Go embeds ``ObjectMeta`` into ``Event`` and ``Pod``, so ``evt.Namespace`` and
``pod.ClusterName`` are promoted field accesses. That promotion is reproduced
with properties, because the first-party code relies on it.
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from ._gojson import is_zero_instant

__all__ = [
    "Time",
    "OwnerReference",
    "ObjectMeta",
    "ObjectReference",
    "EventSource",
    "Event",
    "ContainerStateTerminated",
    "ContainerState",
    "ContainerStatus",
    "PodSpec",
    "PodStatus",
    "Pod",
    "EventTypeNormal",
    "EventTypeWarning",
    "PodFailed",
    "NamespaceAll",
    "now",
]

EventTypeNormal = "Normal"
EventTypeWarning = "Warning"
PodFailed = "Failed"
NamespaceAll = ""

# Go's zero time.Time.
_ZERO = _dt.datetime(1, 1, 1, tzinfo=_dt.timezone.utc)


class Time:
    """Port of ``metav1.Time``: a wrapper whose zero value marshals as null."""

    __slots__ = ("Time",)

    def __init__(self, t: Optional[_dt.datetime] = None) -> None:
        # Go's time.Time always carries a Location, so Sub/After/IsZero are
        # total. A naive datetime would make them raise here, so it is read as
        # UTC rather than left to blow up at the comparison site.
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        self.Time = t if t is not None else _ZERO

    def IsZero(self) -> bool:
        """Port of ``metav1.Time.IsZero``: the instant, matching Go."""
        return is_zero_instant(self.Time)

    def After(self, other: _dt.datetime) -> bool:
        return self.Time > other

    def Sub(self, other: _dt.datetime) -> _dt.timedelta:
        return self.Time - other


def now() -> Time:
    """Port of ``metav1.Now``."""
    return Time(_dt.datetime.now(_dt.timezone.utc))


class OwnerReference:
    __slots__ = ("APIVersion", "Kind", "Name", "Controller")

    def __init__(
        self,
        APIVersion: str = "",
        Kind: str = "",
        Name: str = "",
        Controller: Optional[bool] = None,
    ) -> None:
        self.APIVersion = APIVersion
        self.Kind = Kind
        self.Name = Name
        self.Controller = Controller


class ObjectMeta:
    __slots__ = (
        "Name",
        "Namespace",
        "UID",
        "ClusterName",
        "Labels",
        "OwnerReferences",
        "CreationTimestamp",
    )

    def __init__(
        self,
        Name: str = "",
        Namespace: str = "",
        UID: str = "",
        ClusterName: str = "",
        Labels: Optional[Dict[str, str]] = None,
        OwnerReferences: Optional[List[OwnerReference]] = None,
        CreationTimestamp: Optional[Time] = None,
    ) -> None:
        self.Name = Name
        self.Namespace = Namespace
        self.UID = UID
        self.ClusterName = ClusterName
        self.Labels = Labels
        self.OwnerReferences = OwnerReferences if OwnerReferences is not None else []
        self.CreationTimestamp = CreationTimestamp if CreationTimestamp is not None else Time()

    def GetLabels(self) -> Optional[Dict[str, str]]:
        """Port of ``GetLabels``: Go hands back the **nil map itself**.

        That nil propagates: ``DefaultEventHandler.Tags()`` returns it, and it
        marshals to JSON ``null`` rather than ``{}``. Ranging over a nil map in
        Go is legal and yields nothing, so every call site must tolerate it.
        """
        return self.Labels


class _MetaEmbedded:
    """Reproduces Go's promotion of embedded ObjectMeta fields."""

    ObjectMeta: ObjectMeta

    @property
    def Name(self) -> str:
        return self.ObjectMeta.Name

    @property
    def Namespace(self) -> str:
        return self.ObjectMeta.Namespace

    @property
    def UID(self) -> str:
        return self.ObjectMeta.UID

    @property
    def ClusterName(self) -> str:
        return self.ObjectMeta.ClusterName

    @property
    def Labels(self) -> Optional[Dict[str, str]]:
        return self.ObjectMeta.Labels

    def GetLabels(self) -> Optional[Dict[str, str]]:
        return self.ObjectMeta.GetLabels()


class ObjectReference:
    __slots__ = ("APIVersion", "Kind", "Namespace", "Name", "FieldPath", "ResourceVersion")

    def __init__(
        self,
        APIVersion: str = "",
        Kind: str = "",
        Namespace: str = "",
        Name: str = "",
        FieldPath: str = "",
        ResourceVersion: str = "",
    ) -> None:
        self.APIVersion = APIVersion
        self.Kind = Kind
        self.Namespace = Namespace
        self.Name = Name
        self.FieldPath = FieldPath
        self.ResourceVersion = ResourceVersion


class EventSource:
    __slots__ = ("Component",)

    def __init__(self, Component: str = "") -> None:
        self.Component = Component


class Event(_MetaEmbedded):
    """Port of ``v1.Event``."""

    def __init__(
        self,
        ObjectMeta_: Optional[ObjectMeta] = None,
        Type: str = "",
        Reason: str = "",
        Message: str = "",
        Action: str = "",
        Count: int = 0,
        Source: Optional[EventSource] = None,
        InvolvedObject: Optional[ObjectReference] = None,
    ) -> None:
        self.ObjectMeta = ObjectMeta_ if ObjectMeta_ is not None else ObjectMeta()
        self.Type = Type
        self.Reason = Reason
        self.Message = Message
        self.Action = Action
        self.Count = Count
        self.Source = Source if Source is not None else EventSource()
        self.InvolvedObject = InvolvedObject if InvolvedObject is not None else ObjectReference()


class ContainerStateTerminated:
    __slots__ = ("ExitCode", "Reason", "Message", "FinishedAt")

    def __init__(
        self,
        ExitCode: int = 0,
        Reason: str = "",
        Message: str = "",
        FinishedAt: Optional[Time] = None,
    ) -> None:
        self.ExitCode = ExitCode
        self.Reason = Reason
        self.Message = Message
        self.FinishedAt = FinishedAt if FinishedAt is not None else Time()


class ContainerState:
    __slots__ = ("Terminated",)

    def __init__(self, Terminated: Optional[ContainerStateTerminated] = None) -> None:
        self.Terminated = Terminated


class ContainerStatus:
    __slots__ = ("Name", "Image", "RestartCount", "LastTerminationState")

    def __init__(
        self,
        Name: str = "",
        Image: str = "",
        RestartCount: int = 0,
        LastTerminationState: Optional[ContainerState] = None,
    ) -> None:
        self.Name = Name
        self.Image = Image
        self.RestartCount = RestartCount
        self.LastTerminationState = (
            LastTerminationState if LastTerminationState is not None else ContainerState()
        )


class PodSpec:
    __slots__ = ("NodeName",)

    def __init__(self, NodeName: str = "") -> None:
        self.NodeName = NodeName


class PodStatus:
    __slots__ = ("Phase", "Message", "Reason", "ContainerStatuses", "InitContainerStatuses")

    def __init__(
        self,
        Phase: str = "",
        Message: str = "",
        Reason: str = "",
        ContainerStatuses: Optional[List[ContainerStatus]] = None,
        InitContainerStatuses: Optional[List[ContainerStatus]] = None,
    ) -> None:
        self.Phase = Phase
        self.Message = Message
        self.Reason = Reason
        self.ContainerStatuses = ContainerStatuses if ContainerStatuses is not None else []
        self.InitContainerStatuses = (
            InitContainerStatuses if InitContainerStatuses is not None else []
        )


class Pod(_MetaEmbedded):
    """Port of ``v1.Pod``."""

    def __init__(
        self,
        ObjectMeta_: Optional[ObjectMeta] = None,
        Kind: str = "",
        Spec: Optional[PodSpec] = None,
        Status: Optional[PodStatus] = None,
    ) -> None:
        self.ObjectMeta = ObjectMeta_ if ObjectMeta_ is not None else ObjectMeta()
        self.Kind = Kind
        self.Spec = Spec if Spec is not None else PodSpec()
        self.Status = Status if Status is not None else PodStatus()
