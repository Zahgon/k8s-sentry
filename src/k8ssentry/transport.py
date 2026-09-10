"""The I/O edge: Kubernetes access and Sentry delivery.

Everything here is deliberately outside the byte-exact claim. There is no way to
record a Go oracle for informer wiring or HTTP delivery without a live cluster,
so this module is a thin, documented adapter rather than a proven port. See
``verification/SCOPE.md``.

The third-party clients are imported lazily so the transformation under test —
and the whole test suite — runs with **zero runtime dependencies**.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterator, List, Optional, cast

from . import _k8s as k8s
from . import _sentry as sentry

__all__ = ["SentryHub", "KubernetesClient", "createKubernetesClient", "inClusterConfig"]


class SentryHub:
    """Port of the ``sentry.Capture*`` package functions.

    Delivery goes through ``sentry_sdk`` when it is installed and a DSN is set;
    otherwise events are collected in memory, which is what makes the daemon
    runnable — and testable — without a Sentry account.
    """

    def __init__(self, dsn: str = "", environment: str = "", enabled: bool = True) -> None:
        self.dsn = dsn
        self.environment = environment
        self.enabled = enabled
        self.events: List[sentry.Event] = []
        self._client: Any = None
        if enabled and dsn:
            self._client = self._init_sdk(dsn, environment)

    @staticmethod
    def _init_sdk(dsn: str, environment: str) -> Any:
        try:
            import sentry_sdk  # noqa: PLC0415
        except ImportError:
            return None
        sentry_sdk.init(dsn=dsn, environment=environment or None)
        return sentry_sdk

    def CaptureEvent(self, event: sentry.Event) -> None:
        """Apply the client-side defaults Go's SDK applies, then deliver."""
        if event.ServerName == "":
            event.ServerName = os.uname().nodename if hasattr(os, "uname") else ""
        self.events.append(event)
        if self._client is not None:
            self._client.capture_event(_to_sdk_payload(event))

    def CaptureMessage(self, message: str) -> None:
        event = sentry.NewEvent()
        event.Message = message
        event.Level = sentry.LevelInfo
        self.CaptureEvent(event)

    def CaptureException(self, err: BaseException) -> None:
        event = sentry.NewEvent()
        event.Message = str(err)
        event.Level = sentry.LevelError
        self.CaptureEvent(event)

    def Flush(self, timeout_seconds: float = 1.0) -> None:
        if self._client is not None:
            self._client.flush(timeout=timeout_seconds)


def _to_sdk_payload(event: sentry.Event) -> Dict[str, Any]:
    """Hand the ported Event to ``sentry_sdk`` in the shape it expects."""
    payload: Dict[str, Any] = {
        "message": event.Message,
        "level": event.Level or "info",
        "logger": event.Logger,
        "environment": event.Environment,
        "server_name": event.ServerName,
        "fingerprint": list(event.Fingerprint),
        "tags": dict(event.Tags),
        "extra": dict(event.Extra),
    }
    if event.Release:
        payload["release"] = event.Release
    if event.Timestamp is not None:
        payload["timestamp"] = event.Timestamp
    return payload


class KubernetesClient:
    """The Kubernetes calls this tool makes, over the official Python client."""

    def __init__(self, core_v1: Any = None) -> None:
        self._core = core_v1

    def get_pod(self, namespace: str, name: str, resource_version: str = "") -> Optional[k8s.Pod]:
        """Port of ``clientset.CoreV1().Pods(ns).Get(name, opts)``."""
        if self._core is None:
            return None
        raw = self._core.read_namespaced_pod(name=name, namespace=namespace)
        return pod_from_api(raw)

    def watch(self, resource: str, namespace: str) -> Iterator[Any]:
        """Port of ``cache.NewListWatchFromClient`` + ``NewInformer``."""
        if self._core is None:
            return iter(())
        try:
            from kubernetes import watch as k8s_watch  # noqa: PLC0415
        except ImportError:
            return iter(())
        lister = {
            "pods": self._core.list_namespaced_pod,
            "events": self._core.list_namespaced_event,
        }[resource]
        if namespace == k8s.NamespaceAll:
            lister = {
                "pods": self._core.list_pod_for_all_namespaces,
                "events": self._core.list_event_for_all_namespaces,
            }[resource]
            return cast(Iterator[Any], k8s_watch.Watch().stream(lister))
        return cast(Iterator[Any], k8s_watch.Watch().stream(lister, namespace=namespace))


def _labels(raw: Any) -> Optional[Dict[str, str]]:
    """Decode a label map the way Go decodes ``map[string]string``.

    A nil map stays nil, because ``GetLabels`` hands it straight back and it
    marshals as JSON null. A null VALUE, however, is a string zero value in Go,
    so it becomes ``""`` here; left as ``None`` it would reach the payload
    encoder, which only accepts strings, and take the event handler down.
    """
    if raw is None:
        return None
    return {k: ("" if v is None else v) for k, v in raw.items()}


def _meta_from_api(raw: Any) -> k8s.ObjectMeta:
    meta = getattr(raw, "metadata", None)
    if meta is None:
        return k8s.ObjectMeta()
    owners = [
        k8s.OwnerReference(
            APIVersion=getattr(o, "api_version", "") or "",
            Kind=getattr(o, "kind", "") or "",
            Name=getattr(o, "name", "") or "",
            Controller=getattr(o, "controller", None),
        )
        for o in (getattr(meta, "owner_references", None) or [])
    ]
    created = getattr(meta, "creation_timestamp", None)
    return k8s.ObjectMeta(
        Name=getattr(meta, "name", "") or "",
        Namespace=getattr(meta, "namespace", "") or "",
        UID=getattr(meta, "uid", "") or "",
        ClusterName=getattr(meta, "cluster_name", "") or "",
        Labels=_labels(getattr(meta, "labels", None)),
        OwnerReferences=owners,
        CreationTimestamp=k8s.Time(created) if created is not None else k8s.Time(),
    )


def pod_from_api(raw: Any) -> k8s.Pod:
    """Translate the official client's Pod into the modelled one."""
    status = getattr(raw, "status", None)
    spec = getattr(raw, "spec", None)

    def statuses(attr: str) -> List[k8s.ContainerStatus]:
        out: List[k8s.ContainerStatus] = []
        for cs in getattr(status, attr, None) or []:
            st = k8s.ContainerStatus(
                Name=getattr(cs, "name", "") or "",
                Image=getattr(cs, "image", "") or "",
                RestartCount=getattr(cs, "restart_count", 0) or 0,
            )
            last = getattr(cs, "last_state", None)
            term = getattr(last, "terminated", None) if last is not None else None
            if term is not None:
                st.LastTerminationState = k8s.ContainerState(
                    Terminated=k8s.ContainerStateTerminated(
                        ExitCode=getattr(term, "exit_code", 0) or 0,
                        Reason=getattr(term, "reason", "") or "",
                        Message=getattr(term, "message", "") or "",
                        FinishedAt=k8s.Time(getattr(term, "finished_at", None)),
                    )
                )
            out.append(st)
        return out

    return k8s.Pod(
        ObjectMeta_=_meta_from_api(raw),
        # NOT defaulted to "Pod": the API server strips TypeMeta from the items
        # inside a list or watch response, so `kind` is absent for every object
        # this tool sees, and Go's v1.Pod.Kind is "" there. That empty string
        # reaches Sentry as tags["kind"].
        Kind=getattr(raw, "kind", "") or "",
        Spec=k8s.PodSpec(NodeName=getattr(spec, "node_name", "") or ""),
        Status=k8s.PodStatus(
            Phase=getattr(status, "phase", "") or "",
            Message=getattr(status, "message", "") or "",
            Reason=getattr(status, "reason", "") or "",
            ContainerStatuses=statuses("container_statuses"),
            InitContainerStatuses=statuses("init_container_statuses"),
        ),
    )


def event_from_api(raw: Any) -> k8s.Event:
    """Translate the official client's Event into the modelled one."""
    involved = getattr(raw, "involved_object", None)
    source = getattr(raw, "source", None)
    return k8s.Event(
        ObjectMeta_=_meta_from_api(raw),
        Type=getattr(raw, "type", "") or "",
        Reason=getattr(raw, "reason", "") or "",
        Message=getattr(raw, "message", "") or "",
        Action=getattr(raw, "action", "") or "",
        Count=getattr(raw, "count", 0) or 0,
        Source=k8s.EventSource(Component=getattr(source, "component", "") or ""),
        InvolvedObject=k8s.ObjectReference(
            APIVersion=getattr(involved, "api_version", "") or "",
            Kind=getattr(involved, "kind", "") or "",
            Namespace=getattr(involved, "namespace", "") or "",
            Name=getattr(involved, "name", "") or "",
            FieldPath=getattr(involved, "field_path", "") or "",
            ResourceVersion=getattr(involved, "resource_version", "") or "",
        ),
    )


def inClusterConfig() -> bool:
    """Port of the ``rest.InClusterConfig`` precondition."""
    from .application import inCluster  # noqa: PLC0415

    return inCluster()


def createKubernetesClient(
    configFile: str = "",
) -> "tuple[Optional[KubernetesClient], Optional[BaseException]]":
    """Port of ``createKubernetesClient``, returning Go's ``(client, error)`` pair.

    Falls back to ``~/.kube/config`` when not running in a cluster, exactly as
    the Go original does.
    """
    from .application import inCluster  # noqa: PLC0415

    if configFile == "" and not inCluster():
        home = os.path.expanduser("~")
        if home:
            configFile = os.path.join(home, ".kube", "config")

    try:
        from kubernetes import client as k8s_client  # noqa: PLC0415
        from kubernetes import config as k8s_config  # noqa: PLC0415
    except ImportError as exc:
        return None, exc

    try:
        if configFile == "":
            k8s_config.load_incluster_config()
        else:
            k8s_config.load_kube_config(config_file=configFile)
    except Exception as exc:  # noqa: BLE001 - Go returns this as an error value
        return None, exc

    return KubernetesClient(k8s_client.CoreV1Api()), None


def dispatch(stream: Iterator[Any], on_add: Callable[[Any], None]) -> None:
    """Feed a watch stream to a handler, translating each object once."""
    for item in stream:
        obj = item.get("object") if isinstance(item, dict) else item
        on_add(obj)
