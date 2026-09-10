"""Port of ``handler_pod.go``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _k8s as k8s
from .handler_default import fingerprintFromMeta

__all__ = ["PodEventHandler", "NewPodEventHandler"]


class PodEventHandler:
    """Port of ``PodEventHandler``: events whose involved object is a Pod."""

    __slots__ = ("Pod", "Event")

    def __init__(self, Pod: k8s.Pod, Event: Optional[k8s.Event] = None) -> None:
        self.Pod = Pod
        self.Event = Event

    def Fingerprint(self) -> List[str]:
        """Port of ``Fingerprint``: grouped by the Pod's controller, not the event."""
        return fingerprintFromMeta(self.Pod.ObjectMeta)

    def Tags(self) -> Dict[str, str]:
        """Port of ``Tags``: the Pod's labels plus its node."""
        tags: Dict[str, str] = {}
        for k, v in (self.Pod.Labels or {}).items():
            tags[k] = v
        tags["nodeName"] = self.Pod.Spec.NodeName
        return tags


def NewPodEventHandler(app: Any, evt: k8s.Event) -> Optional[PodEventHandler]:
    """Port of ``NewPodEventHandler``.

    Go fetches the Pod named by the event through the API and reports the error
    to Sentry, returning nil so ``NewEventHandler`` falls back to the default
    handler. That fallback-on-failure behaviour is preserved.
    """
    client = getattr(app, "clientset", None)
    if client is None:
        return None
    try:
        pod = client.get_pod(
            evt.Namespace,
            evt.InvolvedObject.Name,
            resource_version=evt.InvolvedObject.ResourceVersion,
        )
    except Exception as err:  # noqa: BLE001 - Go reports and returns nil
        capture = getattr(app, "capture_exception", None)
        if callable(capture):
            capture(err)
        return None
    if pod is None:
        return None
    return PodEventHandler(Pod=pod, Event=evt)
