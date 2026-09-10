"""Python port of ``wichert/k8s-sentry``.

Watches Kubernetes pods and events and reports failures to Sentry. The
transformation from a Kubernetes object to a Sentry payload is reproduced
byte-for-byte against the Go original; the cluster and Sentry transports sit
behind a thin adapter so the proven core carries no runtime dependencies.
"""

from __future__ import annotations

from . import _k8s as k8s
from . import _lru as lru
from . import _sentry as sentry
from .application import (
    application,
    getSentryLevel,
    inCluster,
    skipEvent,
    terminationKey,
)
from .event_handler import EventHandler, NewEventHandler, handlerRegistry
from .handler_default import (
    DefaultEventHandler,
    NewDefaultEventHandler,
    fingerprintFromMeta,
)
from .handler_pod import NewPodEventHandler, PodEventHandler

__all__ = [
    "application",
    "terminationKey",
    "skipEvent",
    "getSentryLevel",
    "inCluster",
    "EventHandler",
    "NewEventHandler",
    "handlerRegistry",
    "DefaultEventHandler",
    "NewDefaultEventHandler",
    "fingerprintFromMeta",
    "PodEventHandler",
    "NewPodEventHandler",
    "k8s",
    "sentry",
    "lru",
]
