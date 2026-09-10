"""Python side of the Go truth tables.

Replays ``truth-inputs.json`` -- the same file the Go probe consumed -- and emits
the shape the probe recorded, so ``tests/test_differential.py`` can diff them
field by field.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from k8ssentry import _k8s as k8s  # noqa: E402
from k8ssentry import _lru as lru  # noqa: E402
from k8ssentry import _sentry as sentry  # noqa: E402
from k8ssentry._gojson import GoTime  # noqa: E402
from k8ssentry._sentry import _is_zero_time  # noqa: E402
from k8ssentry.application import (  # noqa: E402
    application,
    getSentryLevel,
    inCluster,
    skipEvent,
)
from k8ssentry.handler_default import DefaultEventHandler, fingerprintFromMeta  # noqa: E402
from k8ssentry.handler_pod import PodEventHandler  # noqa: E402

_RE_TIMESTAMP = re.compile(r'"timestamp":"[^"]*"')

# The probe pins the hostname the SDK substitutes when ServerName is left empty.
PROBE_HOST = "probe-host"

# Matches the probe: terminations are pinned relative to a fixed future point so
# the 5ms age gate resolves identically on both sides.
_FUTURE_BASE = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)


class _CapturingHub:
    """Stands in for the Sentry client, applying the same defaults it does.

    Only ``CaptureEvent`` is provided: the corpus never feeds a wrong-typed
    object, so the message/exception paths are unreachable here. If a case ever
    does reach them the missing attribute fails loudly, which beats a silent stub.
    """

    def __init__(self) -> None:
        self.event: Optional[sentry.Event] = None

    def CaptureEvent(self, event: sentry.Event) -> None:
        if event.ServerName == "":
            event.ServerName = PROBE_HOST
        # Go's client stamps a ZERO timestamp, not a nil one: an Event whose
        # CreationTimestamp was unset carries time.Time{}, which IsZero reports.
        if event.Timestamp is None or _is_zero_time(event.Timestamp):
            event.Timestamp = _dt.datetime.now(_dt.timezone.utc)
        self.event = event


def _parse_time(s: str) -> Optional[_dt.datetime]:
    if not s:
        return None
    base = s.rstrip("Z")
    tz = _dt.timezone.utc
    # The offset sign lives after the date, so only look past position 10.
    for sign, mult in (("+", 1), ("-", -1)):
        if sign in base[10:]:
            base, _, off = base.rpartition(sign)
            hh, _, mm = off.partition(":")
            tz = _dt.timezone(mult * _dt.timedelta(hours=int(hh), minutes=int(mm or 0)))
            break
    frac = base.split(".")[1] if "." in base else ""
    nanos = int((frac + "000000000")[:9]) if frac else 0
    naive = _dt.datetime.strptime(base.split(".")[0], "%Y-%m-%dT%H:%M:%S")
    d = GoTime(
        naive.year,
        naive.month,
        naive.day,
        naive.hour,
        naive.minute,
        naive.second,
        nanos // 1000,
        tzinfo=tz,
    )
    if nanos % 1000:
        d._go_nanos = nanos
    return d


def build_meta(m: Dict[str, Any]) -> k8s.ObjectMeta:
    owners = [
        k8s.OwnerReference(
            APIVersion=o.get("api_version", ""),
            Kind=o.get("kind", ""),
            Name=o.get("name", ""),
            Controller=o.get("controller"),
        )
        for o in (m.get("owners") or [])
    ]
    created = _parse_time(m.get("created", "") or "")
    return k8s.ObjectMeta(
        Name=m.get("name", ""),
        Namespace=m.get("namespace", ""),
        UID=m.get("uid", ""),
        ClusterName=m.get("cluster_name", ""),
        Labels=m.get("labels"),
        OwnerReferences=owners,
        CreationTimestamp=k8s.Time(created) if created is not None else k8s.Time(),
    )


def build_event(s: Dict[str, Any]) -> k8s.Event:
    meta = build_meta(s["meta"])
    if s.get("cluster_name"):
        meta.ClusterName = s["cluster_name"]
    io = s["involved_object"]
    return k8s.Event(
        ObjectMeta_=meta,
        Type=s.get("type", ""),
        Reason=s.get("reason", ""),
        Message=s.get("message", ""),
        Action=s.get("action", ""),
        Count=s.get("count", 0),
        Source=k8s.EventSource(Component=s.get("component", "")),
        InvolvedObject=k8s.ObjectReference(
            APIVersion=io.get("api_version", ""),
            Kind=io.get("kind", ""),
            Namespace=io.get("namespace", ""),
            Name=io.get("name", ""),
            FieldPath=io.get("field_path", ""),
            ResourceVersion=io.get("resource_version", ""),
        ),
    )


def build_pod(s: Dict[str, Any]) -> k8s.Pod:
    pod = k8s.Pod(
        ObjectMeta_=build_meta(s["meta"]),
        Kind=s.get("kind", ""),
        Spec=k8s.PodSpec(NodeName=s.get("node_name", "")),
        Status=k8s.PodStatus(
            Phase=s.get("phase", ""),
            Message=s.get("status_message", ""),
            Reason=s.get("status_reason", ""),
        ),
    )
    for c in s.get("containers") or []:
        st = k8s.ContainerStatus(
            Name=c.get("name", ""),
            Image=c.get("image", ""),
            RestartCount=c.get("restart_count", 0),
        )
        if c.get("terminated"):
            finished = _FUTURE_BASE - _dt.timedelta(milliseconds=c.get("finished_ago_ms", 0))
            st.LastTerminationState = k8s.ContainerState(
                Terminated=k8s.ContainerStateTerminated(
                    ExitCode=c.get("exit_code", 0),
                    Reason=c.get("reason", ""),
                    Message=c.get("message", ""),
                    FinishedAt=k8s.Time(finished),
                )
            )
        if c.get("init"):
            pod.Status.InitContainerStatuses.append(st)
        else:
            pod.Status.ContainerStatuses.append(st)
    return pod


def run(inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Replay the corpus in the exact order the Go probe appends its cases."""
    cases: List[Dict[str, Any]] = []

    for ty in inputs["event_types"]:
        evt = k8s.Event(Type=ty)
        cases.append(
            {
                "kind": "eventtype",
                "in": ty,
                "out": [skipEvent(evt), getSentryLevel(evt)],
                "err": None,
            }
        )

    for pair in inputs["env_pairs"]:
        os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        os.environ.pop("KUBERNETES_SERVICE_PORT", None)
        if pair[0] != "":
            os.environ["KUBERNETES_SERVICE_HOST"] = pair[0]
        if pair[1] != "":
            os.environ["KUBERNETES_SERVICE_PORT"] = pair[1]
        cases.append({"kind": "incluster", "in": pair, "out": inCluster(), "err": None})
    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ.pop("KUBERNETES_SERVICE_PORT", None)

    for m in inputs["metas"]:
        cases.append(
            {
                "kind": "fingerprintmeta",
                "in": m,
                "out": fingerprintFromMeta(build_meta(m)),
                "err": None,
            }
        )

    for s in inputs["events"]:
        evt = build_event(s)
        h = DefaultEventHandler(Event=evt)
        cases.append(
            {"kind": "defaulthandler", "in": s, "out": [h.Fingerprint(), h.Tags()], "err": None}
        )

        io = s["involved_object"]
        if not (io.get("api_version") == "v1" and io.get("kind") == "Pod"):
            hub = _CapturingHub()
            app = application(defaultEnvironment=s["meta"].get("cluster_name", ""), hub=hub)
            app.handleEventAdd(evt)
            out = hub.event.MarshalJSON() if hub.event is not None else None
            created = evt.ObjectMeta.CreationTimestamp
            if out is not None and created.IsZero():
                out = _RE_TIMESTAMP.sub('"timestamp":"<T>"', out)
            cases.append({"kind": "eventadd", "in": s, "out": out, "err": None})

    for s in inputs["pods"]:
        pod = build_pod(s)
        ph = PodEventHandler(Pod=pod)
        cases.append(
            {"kind": "podhandler", "in": s, "out": [ph.Fingerprint(), ph.Tags()], "err": None}
        )

        hub = _CapturingHub()
        app = application(terminationsSeen=lru.New(500), defaultEnvironment="", hub=hub)
        app.handlePodUpdate(None, pod)
        out = hub.event.MarshalJSON() if hub.event is not None else None
        if out is not None:
            out = _RE_TIMESTAMP.sub('"timestamp":"<T>"', out)
        cases.append({"kind": "podupdate", "in": s, "out": out, "err": None})

    for seq in inputs.get("pod_seqs", []):
        hub = _CapturingHub()
        app = application(terminationsSeen=lru.New(500), defaultEnvironment="", hub=hub)
        outs = []
        for ps in seq:
            hub.event = None
            app.handlePodUpdate(None, build_pod(ps))
            o = hub.event.MarshalJSON() if hub.event is not None else None
            if o is not None:
                o = _RE_TIMESTAMP.sub('"timestamp":"<T>"', o)
            outs.append(o)
        cases.append({"kind": "podseq", "in": seq, "out": outs, "err": None})

    for s in inputs["sentry_raw"]:
        e = sentry.NewEvent()
        e.Message = s.get("message", "")
        e.Level = s.get("level", "")
        e.Logger = s.get("logger", "")
        e.Environment = s.get("environment", "")
        e.ServerName = s.get("server_name", "")
        e.Release = s.get("release", "")
        e.Fingerprint = list(s.get("fingerprint") or [])
        for k, v in (s.get("tags") or {}).items():
            e.Tags[k] = v
        for k, v in (s.get("extra_str") or {}).items():
            e.Extra[k] = v
        for k, v in (s.get("extra_int") or {}).items():
            e.Extra[k] = v
        ts = _parse_time(s.get("timestamp", "") or "")
        if ts is not None:
            e.Timestamp = ts
        cases.append({"kind": "sentryjson", "in": s, "out": e.MarshalJSON(), "err": None})

    return cases


def main() -> None:
    with open(os.path.join(_HERE, "truth-inputs.json"), encoding="utf-8") as fh:
        inputs = json.load(fh)
    cases = run(inputs)
    with open(os.path.join(_HERE, "py-truth.json"), "w", encoding="utf-8") as fh:
        json.dump(cases, fh, ensure_ascii=False)
        fh.write("\n")
    print("replayed {} cases".format(len(cases)))


if __name__ == "__main__":
    main()
