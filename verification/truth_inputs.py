"""Generate the shared corpus both implementations are driven with.

Written to ``truth-inputs.json``; the Go probe reads it and ``py_truth.py``
replays the identical file, so the two sides cannot drift.
"""

from __future__ import annotations

import json
import os


def _event_types():
    """Drives skipEvent and getSentryLevel together."""
    return [
        "Normal",
        "Warning",
        "Error",
        "Unknown",
        "",
        "warning",
        "error",
        "normal",
        "NORMAL",
        "Info",
        "Failed",
        "  ",
        "Warning ",
        "Erro",
        "Errorr",
    ]


def _env_pairs():
    return [
        ["", ""],
        ["api", ""],
        ["", "4138"],
        ["api", "4138"],
        ["api.cluster.local", "443"],
        [" ", " "],
        ["0", "0"],
    ]


def _metas():
    ctrl_true, ctrl_false = True, False
    base = {
        "name": "",
        "namespace": "",
        "uid": "",
        "cluster_name": "",
        "labels": None,
        "owners": [],
        "created": "",
    }

    def meta(**kw):
        m = dict(base)
        m.update(kw)
        return m

    return [
        meta(),
        meta(namespace="default", uid="abc-123"),
        meta(name="my-pod", namespace="prod", uid="uid-1"),
        # A controller owner wins over the object's own identity.
        meta(
            namespace="prod",
            uid="uid-2",
            owners=[
                {
                    "api_version": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "rs-1",
                    "controller": ctrl_true,
                }
            ],
        ),
        # Controller=false must be ignored.
        meta(
            namespace="prod",
            uid="uid-3",
            owners=[
                {
                    "api_version": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "rs-2",
                    "controller": ctrl_false,
                }
            ],
        ),
        # Controller=nil must be ignored.
        meta(
            namespace="prod",
            uid="uid-4",
            owners=[
                {"api_version": "apps/v1", "kind": "ReplicaSet", "name": "rs-3", "controller": None}
            ],
        ),
        # First controller owner wins.
        meta(
            namespace="prod",
            uid="uid-5",
            owners=[
                {"api_version": "v1", "kind": "Node", "name": "n1", "controller": ctrl_false},
                {
                    "api_version": "apps/v1",
                    "kind": "Deployment",
                    "name": "d1",
                    "controller": ctrl_true,
                },
                {"api_version": "batch/v1", "kind": "Job", "name": "j1", "controller": ctrl_true},
            ],
        ),
        meta(namespace="ns", uid="uid-6", labels={"app": "web"}),
        meta(namespace="ns", uid="uid-7", labels={"app": "web", "tier": "front"}),
        meta(namespace="", uid="only-uid"),
        meta(namespace="ns-only", uid=""),
        meta(cluster_name="c1", namespace="ns", uid="uid-8"),
    ]


def _events():
    def ev(**kw):
        base = {
            "meta": {
                "name": "evt",
                "namespace": "default",
                "uid": "evt-uid",
                "cluster_name": "",
                "labels": None,
                "owners": [],
                "created": "2020-10-05T11:39:18.123456789Z",
            },
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "action": "",
            "count": 1,
            "component": "kubelet",
            "cluster_name": "",
            "involved_object": {
                "api_version": "v1",
                "kind": "Deployment",
                "namespace": "default",
                "name": "web",
                "field_path": "",
                "resource_version": "",
            },
        }
        base.update(kw)
        return base

    out = [
        ev(),
        ev(type="Error"),
        ev(type="Normal"),
        ev(type="Unknown"),
        ev(action="Binding"),
        ev(count=0),
        ev(count=42),
        ev(cluster_name="prod-cluster"),
        ev(message=""),
        ev(reason=""),
        ev(component=""),
        # A Pod involved object would call the API, so the registry must fall
        # through to the default handler for everything else.
        ev(
            involved_object={
                "api_version": "apps/v1",
                "kind": "Pod",
                "namespace": "d",
                "name": "p",
                "field_path": "",
                "resource_version": "",
            }
        ),
        ev(
            involved_object={
                "api_version": "v1",
                "kind": "Node",
                "namespace": "",
                "name": "node-1",
                "field_path": "spec.containers{web}",
                "resource_version": "99",
            }
        ),
        ev(
            meta={
                "name": "evt2",
                "namespace": "kube-system",
                "uid": "u2",
                "cluster_name": "",
                "labels": {"k": "v"},
                "owners": [],
                "created": "2020-01-01T00:00:00Z",
            }
        ),
        ev(
            meta={
                "name": "evt3",
                "namespace": "ns",
                "uid": "u3",
                "cluster_name": "",
                "labels": None,
                "owners": [],
                "created": "",
            }
        ),
        ev(message='quoted "msg" and <html> & more'),
        ev(message="unicode héllo → 日本語"),
    ]
    # Zero-INSTANT creation timestamps. Go's IsZero compares the instant, so a
    # non-UTC offset or a sub-microsecond digit changes whether the payload
    # carries a timestamp at all.
    for name, created in (
        ("z1", "0001-01-01T00:00:00Z"),
        ("z2", "0001-01-01T00:00:00.000000001Z"),
        ("z3", "0001-01-01T00:00:00-01:00"),
        ("z4", "0001-01-01T01:00:00+01:00"),
        ("z5", "0001-01-01T00:00:00.000001Z"),
    ):
        out.append(
            ev(
                meta={
                    "name": name,
                    "namespace": "ns",
                    "uid": name,
                    "cluster_name": "",
                    "labels": None,
                    "owners": [],
                    "created": created,
                }
            )
        )
    return out


def _pods():
    def pod(**kw):
        base = {
            "meta": {
                "name": "web-1",
                "namespace": "default",
                "uid": "pod-uid",
                "cluster_name": "",
                "labels": None,
                "owners": [],
                "created": "",
            },
            "kind": "Pod",
            "phase": "Running",
            "status_message": "",
            "status_reason": "",
            "node_name": "node-1",
            "containers": [],
        }
        base.update(kw)
        return base

    def container(**kw):
        base = {
            "name": "web",
            "image": "nginx:1.0",
            "restart_count": 0,
            "terminated": False,
            "exit_code": 0,
            "reason": "",
            "message": "",
            "finished_ago_ms": 0,
            "init": False,
        }
        base.update(kw)
        return base

    return [
        # PodFailed branch: no timing involved, fully deterministic.
        pod(phase="Failed", status_message="boom", status_reason="Evicted"),
        pod(phase="Failed", status_message="", status_reason=""),
        pod(
            phase="Failed",
            status_message="oom",
            status_reason="OOMKilled",
            meta={
                "name": "p2",
                "namespace": "prod",
                "uid": "u2",
                "cluster_name": "c1",
                "labels": {"app": "web"},
                "owners": [],
                "created": "",
            },
        ),
        pod(
            phase="Failed",
            status_message="m",
            status_reason="r",
            meta={
                "name": "p3",
                "namespace": "prod",
                "uid": "u3",
                "cluster_name": "",
                "labels": None,
                "owners": [
                    {
                        "api_version": "apps/v1",
                        "kind": "ReplicaSet",
                        "name": "rs",
                        "controller": True,
                    }
                ],
                "created": "",
            },
        ),
        pod(phase="Failed", status_message="m", status_reason="r", node_name=""),
        # Running with no terminated container: nothing should be captured.
        pod(phase="Running", containers=[container()]),
        pod(phase="Succeeded", containers=[container()]),
        # Terminated with exit code 0: ignored.
        pod(phase="Running", containers=[container(terminated=True, exit_code=0)]),
        # Terminated non-zero, just now: reported.
        pod(
            phase="Running",
            containers=[
                container(
                    terminated=True,
                    exit_code=137,
                    reason="OOMKilled",
                    message="",
                    restart_count=3,
                    finished_ago_ms=0,
                )
            ],
        ),
        pod(
            phase="Running",
            containers=[
                container(
                    terminated=True,
                    exit_code=1,
                    reason="Error",
                    message="crash",
                    restart_count=1,
                    finished_ago_ms=0,
                )
            ],
        ),
        # Init containers come first in the scan order.
        pod(
            phase="Running",
            containers=[
                container(
                    name="init",
                    init=True,
                    terminated=True,
                    exit_code=2,
                    reason="Error",
                    message="init failed",
                    finished_ago_ms=0,
                ),
                container(
                    name="web",
                    terminated=True,
                    exit_code=3,
                    reason="Error",
                    message="web failed",
                    finished_ago_ms=0,
                ),
            ],
        ),
        # Old termination: skipped by the 5ms age window.
        pod(
            phase="Running",
            containers=[
                container(terminated=True, exit_code=1, reason="Error", finished_ago_ms=5000)
            ],
        ),
        pod(phase="Running", labels_probe=None)
        if False
        else pod(
            phase="Running",
            meta={
                "name": "p9",
                "namespace": "ns",
                "uid": "u9",
                "cluster_name": "",
                "labels": {"a": "b", "c": "d"},
                "owners": [],
                "created": "",
            },
            containers=[
                container(
                    terminated=True, exit_code=9, reason="Error", message="msg", finished_ago_ms=0
                )
            ],
        ),
    ]


def _sentry_raw():
    """Direct Event marshalling, independent of the k8s plumbing."""

    def spec(**kw):
        base = {
            "message": "",
            "level": "",
            "logger": "",
            "environment": "",
            "server_name": "",
            "release": "",
            "fingerprint": None,
            "tags": None,
            "extra_str": None,
            "extra_int": None,
            "timestamp": "",
        }
        base.update(kw)
        return base

    return [
        spec(),
        spec(message="m"),
        spec(message="m", level="error"),
        spec(message="m", level="warning", logger="kubernetes"),
        spec(environment="prod", server_name="node-1", release="img:1"),
        spec(fingerprint=["a", "b"]),
        spec(fingerprint=[]),
        spec(fingerprint=[""]),
        spec(tags={"reason": "OOMKilled"}),
        spec(tags={"b": "2", "a": "1", "c": "3"}),
        spec(extra_str={"exit-code": "137"}),
        spec(extra_int={"restartCount": 3}),
        spec(extra_str={"x": "1"}, extra_int={"y": 2}),
        spec(timestamp="2020-10-05T11:39:18.123456789Z"),
        spec(timestamp="2020-10-05T11:39:18Z"),
        spec(timestamp="2020-10-05T13:39:18+02:00"),
        spec(timestamp="0001-01-01T00:00:00Z"),
        spec(timestamp="0001-01-01T00:00:00.000000001Z"),
        spec(timestamp="0001-01-01T00:00:00-01:00"),
        spec(timestamp="0001-01-01T01:00:00+01:00"),
        spec(timestamp="0001-01-01T00:00:00.000001Z"),
        spec(message='quoted "x" <html> & amp'),
        spec(message="héllo → 日本語"),
        spec(message="new\nline\ttab"),
    ]


def _pod_seqs():
    """Sequences sharing one LRU -- the only way to reach the terminated branch.

    ``isNewTermination`` is false on first sighting, so a single update can
    never report a terminated container. Each sequence therefore re-sends the
    same pod with a LATER FinishedAt. ``finished_ago_ms`` is subtracted from a
    fixed point one hour in the future, so a SMALLER value is a LATER time.
    """

    def pod(containers, **kw):
        base = {
            "meta": {
                "name": "web-1",
                "namespace": "default",
                "uid": "pod-uid",
                "cluster_name": "",
                "labels": None,
                "owners": [],
                "created": "",
            },
            "kind": "Pod",
            "phase": "Running",
            "status_message": "",
            "status_reason": "",
            "node_name": "node-1",
            "containers": containers,
        }
        base.update(kw)
        return base

    def c(**kw):
        base = {
            "name": "web",
            "image": "nginx:1.0",
            "restart_count": 0,
            "terminated": True,
            "exit_code": 1,
            "reason": "Error",
            "message": "",
            "finished_ago_ms": 1000,
            "init": False,
        }
        base.update(kw)
        return base

    return [
        # Same FinishedAt twice: never reported.
        [pod([c()]), pod([c()])],
        # Newer FinishedAt on the second update: reported.
        [pod([c(finished_ago_ms=1000)]), pod([c(finished_ago_ms=500)])],
        # Older FinishedAt on the second update: not reported.
        [pod([c(finished_ago_ms=500)]), pod([c(finished_ago_ms=1000)])],
        # OOMKilled leaves no message, so Reason is used instead.
        [
            pod([c(reason="OOMKilled", message="", restart_count=3, exit_code=137)]),
            pod(
                [
                    c(
                        reason="OOMKilled",
                        message="",
                        restart_count=3,
                        exit_code=137,
                        finished_ago_ms=500,
                    )
                ]
            ),
        ],
        # Init containers are scanned first, so the init one wins.
        [
            pod(
                [
                    c(name="init", init=True, exit_code=2, message="init boom"),
                    c(name="web", exit_code=3, message="web boom"),
                ]
            ),
            pod(
                [
                    c(
                        name="init",
                        init=True,
                        exit_code=2,
                        message="init boom",
                        finished_ago_ms=500,
                    ),
                    c(name="web", exit_code=3, message="web boom", finished_ago_ms=500),
                ]
            ),
        ],
        # Exit code 0 is ignored even when newer.
        [pod([c(exit_code=0)]), pod([c(exit_code=0, finished_ago_ms=500)])],
        # Negative exit code.
        [pod([c(exit_code=-1)]), pod([c(exit_code=-1, finished_ago_ms=500)])],
        # A label may overwrite reason/namespace/kind.
        [
            pod(
                [c()],
                meta={
                    "name": "p",
                    "namespace": "ns",
                    "uid": "u",
                    "cluster_name": "",
                    "labels": {"reason": "LABEL", "namespace": "LBLNS", "kind": "LBLKIND"},
                    "owners": [],
                    "created": "",
                },
            ),
            pod(
                [c(finished_ago_ms=500)],
                meta={
                    "name": "p",
                    "namespace": "ns",
                    "uid": "u",
                    "cluster_name": "",
                    "labels": {"reason": "LABEL", "namespace": "LBLNS", "kind": "LBLKIND"},
                    "owners": [],
                    "created": "",
                },
            ),
        ],
        # Cluster name set.
        [
            pod(
                [c()],
                meta={
                    "name": "p",
                    "namespace": "ns",
                    "uid": "u",
                    "cluster_name": "c1",
                    "labels": None,
                    "owners": [],
                    "created": "",
                },
            ),
            pod(
                [c(finished_ago_ms=500)],
                meta={
                    "name": "p",
                    "namespace": "ns",
                    "uid": "u",
                    "cluster_name": "c1",
                    "labels": None,
                    "owners": [],
                    "created": "",
                },
            ),
        ],
        # Non-ASCII and control characters through every string field.
        [
            pod([c(message="héllo → <b>&", reason="Er\tror")]),
            pod([c(message="héllo → <b>&", reason="Er\tror", finished_ago_ms=500)]),
        ],
    ]


def build():
    return {
        "event_types": _event_types(),
        "env_pairs": _env_pairs(),
        "metas": _metas(),
        "events": _events(),
        "pods": _pods(),
        "sentry_raw": _sentry_raw(),
        "pod_seqs": _pod_seqs(),
    }


def main() -> None:
    data = build()
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "truth-inputs.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
        fh.write("\n")
    print("wrote {} probe inputs".format(sum(len(v) for v in data.values())))


if __name__ == "__main__":
    main()
