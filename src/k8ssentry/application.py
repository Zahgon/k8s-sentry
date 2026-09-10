"""Port of ``application.go``.

The event-construction here is the product: given a Kubernetes Pod or Event,
build the Sentry payload. It is a pure transformation, and
``verification/go-truth.json`` pins it byte-for-byte against real Go.

The cluster plumbing (informers, watches) is deliberately not part of this
module; it lives behind :mod:`k8ssentry.transport` so the transformation stays
dependency-free and testable.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from typing import Any, Callable, List, NamedTuple, Optional

from . import _k8s as k8s
from . import _lru as lru
from . import _sentry as sentry
from .event_handler import NewEventHandler
from .handler_default import fingerprintFromMeta

__all__ = [
    "terminationKey",
    "application",
    "skipEvent",
    "getSentryLevel",
    "inCluster",
]


class terminationKey(NamedTuple):
    """Port of ``terminationKey``."""

    podUID: str
    containerName: str


class application:
    """Port of ``application``."""

    def __init__(
        self,
        clientset: Any = None,
        defaultEnvironment: str = "",
        namespaces: Optional[List[str]] = None,
        terminationsSeen: Optional[lru.Cache] = None,
        hub: Any = None,
    ) -> None:
        self.clientset = clientset
        self.defaultEnvironment = defaultEnvironment
        self.namespaces = namespaces if namespaces is not None else []
        self.terminationsSeen = terminationsSeen
        # Where CaptureEvent/CaptureMessage go. Injected so the transformation
        # can be exercised without a Sentry transport.
        self.hub = hub

    # -- daemon ----------------------------------------------------------

    def Run(self) -> "tuple[threading.Event, Optional[BaseException]]":
        """Port of ``Run``: returns Go's ``(stop, error)`` pair.

        Go starts two goroutines per namespace; here each becomes a daemon
        thread watching the same stop signal.
        """
        try:
            self.terminationsSeen = lru.New(500)
        except Exception as err:  # noqa: BLE001 - Go returns (nil, err)
            return threading.Event(), err

        stop = threading.Event()
        for namespace in self.namespaces:
            threading.Thread(target=self.monitorEvents, args=(namespace, stop), daemon=True).start()
            threading.Thread(target=self.monitorPods, args=(namespace, stop), daemon=True).start()
        return stop, None

    def monitorPods(self, namespace: str, stop: "threading.Event") -> None:
        """Port of ``monitorPods``: pod updates drive handlePodUpdate."""
        self._monitor(namespace, stop, "pods", self._on_pod)

    def monitorEvents(self, namespace: str, stop: "threading.Event") -> None:
        """Port of ``monitorEvents``: event additions drive handleEventAdd."""
        self._monitor(namespace, stop, "events", self._on_event)

    def _monitor(
        self,
        namespace: str,
        stop: "threading.Event",
        resource: str,
        handler: "Callable[[Any], None]",
    ) -> None:
        watcher = getattr(self.clientset, "watch", None)
        if not callable(watcher):
            return
        for raw in watcher(resource, namespace):
            if stop.is_set():
                return
            handler(raw.get("object") if isinstance(raw, dict) else raw)

    def _on_pod(self, raw: Any) -> None:
        from .transport import pod_from_api  # noqa: PLC0415

        self.handlePodUpdate(None, raw if isinstance(raw, k8s.Pod) else pod_from_api(raw))

    def _on_event(self, raw: Any) -> None:
        from .transport import event_from_api  # noqa: PLC0415

        self.handleEventAdd(raw if isinstance(raw, k8s.Event) else event_from_api(raw))

    # -- capture helpers -------------------------------------------------

    def capture_event(self, event: sentry.Event) -> None:
        if self.hub is not None:
            self.hub.CaptureEvent(event)

    def capture_message(self, message: str) -> None:
        if self.hub is not None:
            self.hub.CaptureMessage(message)

    def capture_exception(self, err: BaseException) -> None:
        if self.hub is not None:
            self.hub.CaptureException(err)

    # -- pod path --------------------------------------------------------

    def handlePodUpdate(self, oldObj: Any, newObj: Any) -> None:
        """Port of ``handlePodUpdate``."""
        if not isinstance(newObj, k8s.Pod):
            self.capture_message("Unexpected pod type")
            return
        pod = newObj

        sentryEvent: Optional[sentry.Event] = None

        if pod.Status.Phase == k8s.PodFailed:
            # Every container has terminated and at least one failed.
            sentryEvent = sentry.NewEvent()
            sentryEvent.Message = pod.Status.Message
            sentryEvent.ServerName = pod.Spec.NodeName
            sentryEvent.Tags["reason"] = pod.Status.Reason
        else:
            # Still running: look for a container that terminated non-zero.
            # Go appends the regular statuses onto the init statuses, so init
            # containers are scanned first, and only the FIRST match is reported.
            allContainers = list(pod.Status.InitContainerStatuses) + list(
                pod.Status.ContainerStatuses
            )
            for status in allContainers:
                term = status.LastTerminationState.Terminated
                if term is not None and term.ExitCode != 0 and self.isNewTermination(pod, status):
                    sentryEvent = sentry.NewEvent()
                    sentryEvent.Message = term.Message
                    sentryEvent.ServerName = pod.Spec.NodeName
                    if sentryEvent.Message == "":
                        # OOMKilled does not leave a message.
                        sentryEvent.Message = term.Reason
                    sentryEvent.Release = status.Image
                    sentryEvent.Tags["reason"] = term.Reason
                    sentryEvent.Extra["exit-code"] = str(int(term.ExitCode))
                    sentryEvent.Extra["restartCount"] = status.RestartCount
                    break

        if sentryEvent is not None:
            sentryEvent.Logger = "kubernetes"
            sentryEvent.Level = sentry.LevelError
            if self.defaultEnvironment != "":
                sentryEvent.Environment = self.defaultEnvironment
            else:
                sentryEvent.Environment = pod.Namespace

            sentryEvent.Fingerprint = [
                sentryEvent.Tags["reason"],
                sentryEvent.Message,
            ] + fingerprintFromMeta(pod.ObjectMeta)

            sentryEvent.Tags["namespace"] = pod.Namespace
            if pod.ClusterName != "":
                sentryEvent.Tags["cluster"] = pod.ClusterName
            sentryEvent.Tags["kind"] = pod.Kind
            for k, v in (pod.ObjectMeta.GetLabels() or {}).items():
                sentryEvent.Tags[k] = v
            sentryEvent.Message = "Pod/{}: {}".format(pod.Name, sentryEvent.Message)

            self.capture_event(sentryEvent)

    def isNewTermination(self, pod: k8s.Pod, status: k8s.ContainerStatus) -> bool:
        """Port of ``isNewTermination``.

        Note the ordering: the cache is written **before** the age check, so a
        stale record still refreshes the entry. And ``age.Microseconds()`` is a
        truncating conversion, so the window is 5000us inclusive.
        """
        term = status.LastTerminationState.Terminated
        assert term is not None
        finishedAt = term.FinishedAt
        age = k8s.now().Time - finishedAt.Time

        key = terminationKey(podUID=str(pod.UID), containerName=status.Name)
        assert self.terminationsSeen is not None
        cachedTime, seen = self.terminationsSeen.Get(key)
        self.terminationsSeen.Add(key, finishedAt)

        # Skip old records: a container that terminated earlier still has its
        # termination state attached when the pod is updated for other reasons.
        # Go: age.Microseconds(), an exact integer division. Going via
        # total_seconds() is a double float rounding and is off by one on ~1.8%
        # of microsecond values.
        if age // _dt.timedelta(microseconds=1) > 5000:
            return False

        if not isinstance(cachedTime, k8s.Time):
            # Bad data in the cache; do nothing further.
            return False

        return (not seen) or finishedAt.After(cachedTime.Time)

    # -- event path ------------------------------------------------------

    def handleEventAdd(self, obj: Any) -> None:
        """Port of ``handleEventAdd``."""
        if not isinstance(obj, k8s.Event):
            self.capture_message("Unexpected event type")
            return
        evt = obj

        if skipEvent(evt):
            return

        sentryEvent = sentry.NewEvent()
        if self.defaultEnvironment != "":
            sentryEvent.Environment = self.defaultEnvironment
        else:
            sentryEvent.Environment = evt.InvolvedObject.Namespace

        sentryEvent.Logger = "kubernetes"
        sentryEvent.Message = "{}/{}: {}".format(
            evt.InvolvedObject.Kind, evt.InvolvedObject.Name, evt.Message
        )
        sentryEvent.Level = getSentryLevel(evt)
        sentryEvent.Timestamp = evt.ObjectMeta.CreationTimestamp.Time
        sentryEvent.Fingerprint = [
            evt.Source.Component,
            evt.Type,
            evt.Reason,
            evt.Message,
        ]

        sentryEvent.Tags["namespace"] = evt.InvolvedObject.Namespace
        sentryEvent.Tags["component"] = evt.Source.Component
        if evt.ClusterName != "":
            sentryEvent.Tags["cluster"] = evt.ClusterName
        sentryEvent.Tags["reason"] = evt.Reason
        sentryEvent.Tags["kind"] = evt.InvolvedObject.Kind
        sentryEvent.Tags["type"] = evt.Type
        if evt.Action != "":
            sentryEvent.Extra["action"] = evt.Action
        sentryEvent.Extra["count"] = evt.Count

        handler = NewEventHandler(self, evt)
        sentryEvent.Fingerprint = sentryEvent.Fingerprint + list(handler.Fingerprint())
        for k, v in (handler.Tags() or {}).items():
            sentryEvent.Tags[k] = v

        # Go uses log.Printf, whose default LstdFlags prefix the line.
        _log("{} {}".format(evt.Type, sentryEvent.Message))
        self.capture_event(sentryEvent)


def _log(message: str) -> None:
    """Port of ``log.Printf`` with Go's default ``LstdFlags``."""
    stamp = _dt.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    print("{} {}".format(stamp, message), file=sys.stderr)


def skipEvent(evt: k8s.Event) -> bool:
    """Port of ``skipEvent``: only ``Normal`` events are dropped."""
    return evt.Type == k8s.EventTypeNormal


def getSentryLevel(evt: k8s.Event) -> sentry.Level:
    """Port of ``getSentryLevel``.

    Only the exact strings ``Warning`` and ``Error`` map to a level; anything
    else, including different casing, prints a notice and falls back to info.
    """
    if evt.Type == k8s.EventTypeWarning:
        return sentry.LevelWarning
    if evt.Type == "Error":
        return sentry.LevelError
    print("Unexpected event type: {}".format(evt.Type))
    return sentry.LevelInfo


def inCluster() -> bool:
    """Port of ``inCluster``: both service variables must be set."""
    return (
        os.environ.get("KUBERNETES_SERVICE_HOST", "") != ""
        and os.environ.get("KUBERNETES_SERVICE_PORT", "") != ""
    )
