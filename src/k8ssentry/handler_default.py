"""Port of ``handler_default.go``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _k8s as k8s

__all__ = [
    "DefaultEventHandler",
    "NewDefaultEventHandler",
    "fingerprintFromMeta",
]


class DefaultEventHandler:
    """Port of ``DefaultEventHandler``: the fallback for any event kind."""

    __slots__ = ("Event",)

    def __init__(self, Event: k8s.Event) -> None:
        self.Event = Event

    def Fingerprint(self) -> List[str]:
        """Port of ``Fingerprint``: the involved object's identity."""
        io = self.Event.InvolvedObject
        return [io.APIVersion, io.Kind, io.Namespace, io.Name, io.FieldPath]

    def Tags(self) -> Optional[Dict[str, str]]:
        """Port of ``Tags``: the event's own labels, nil map included."""
        return self.Event.GetLabels()


def NewDefaultEventHandler(app: Any, evt: k8s.Event) -> DefaultEventHandler:
    """Port of ``NewDefaultEventHandler``."""
    return DefaultEventHandler(Event=evt)


def fingerprintFromMeta(resource: k8s.ObjectMeta) -> List[str]:
    """Port of ``fingerprintFromMeta``.

    A controlling owner wins, so every Pod of a ReplicaSet groups together. Note
    Go checks ``owner.Controller != nil && *owner.Controller`` -- an owner with
    no controller flag, or with it set false, is skipped.
    """
    for owner in resource.OwnerReferences:
        if owner.Controller is not None and owner.Controller:
            return [owner.APIVersion, owner.Kind, owner.Name]

    return [resource.Namespace, str(resource.UID)]
