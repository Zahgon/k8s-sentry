"""Port of ``event_handler.go``."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from . import _k8s as k8s
from .handler_default import NewDefaultEventHandler
from .handler_pod import NewPodEventHandler

__all__ = ["EventHandler", "NewEventHandler", "handlerRegistry", "registryKey"]


class EventHandler(Protocol):
    """Port of the ``EventHandler`` interface."""

    def Fingerprint(self) -> List[str]: ...

    # Optional because Go's GetLabels hands back the nil map, which the default
    # handler returns unchanged.
    def Tags(self) -> Optional[Dict[str, str]]: ...


registryKey = Tuple[str, str]

# Port of ``handlerRegistry``. The key is (APIVersion, Kind).
handlerRegistry: Dict[registryKey, Callable[[Any, k8s.Event], Optional[EventHandler]]] = {
    ("v1", "Pod"): NewPodEventHandler,
}


def NewEventHandler(app: Any, evt: k8s.Event) -> EventHandler:
    """Port of ``NewEventHandler``.

    A registered factory that returns nil falls through to the default handler,
    which is how a Pod whose API lookup failed still produces an event.
    """
    key = (evt.InvolvedObject.APIVersion, evt.InvolvedObject.Kind)
    factory = handlerRegistry.get(key)
    if factory is not None:
        handler = factory(app, evt)
        if handler is not None:
            return handler
    return NewDefaultEventHandler(app, evt)
