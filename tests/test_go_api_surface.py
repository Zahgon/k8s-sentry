"""Every exported identifier of the Go program, exercised at least once.

The Go program exports no package API (it is ``package main``), so the surface
under test is the set of top-level declarations: the handlers, the registry, the
pure predicates, and the daemon wiring. Nothing may be a stub, and nothing may be
reachable only through the differential corpus.
"""

from __future__ import annotations

import datetime as _dt
import io
import os
import threading

import pytest

import k8ssentry
from k8ssentry import _gojson as gojson
from k8ssentry import _k8s as k8s
from k8ssentry import _lru as lru
from k8ssentry import _sentry as sentry
from k8ssentry import main as cli
from k8ssentry import transport
from k8ssentry.application import application, terminationKey
from k8ssentry.event_handler import NewEventHandler, handlerRegistry
from k8ssentry.handler_default import DefaultEventHandler, fingerprintFromMeta
from k8ssentry.handler_pod import NewPodEventHandler, PodEventHandler

_GO_TOP_LEVEL = [
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
]


class _Hub:
    def __init__(self):
        self.events = []
        self.messages = []
        self.exceptions = []

    def CaptureEvent(self, e):
        self.events.append(e)

    def CaptureMessage(self, m):
        self.messages.append(m)

    def CaptureException(self, e):
        self.exceptions.append(e)


def _event(**kw):
    kw.setdefault("Type", "Warning")
    kw.setdefault("InvolvedObject", k8s.ObjectReference(APIVersion="v1", Kind="Node", Name="n"))
    return k8s.Event(**kw)


def _pod(**kw):
    kw.setdefault("Kind", "Pod")
    return k8s.Pod(**kw)


# --------------------------------------------------------------------------
# Surface
# --------------------------------------------------------------------------


def test_every_go_top_level_declaration_is_exported():
    for name in _GO_TOP_LEVEL:
        assert hasattr(k8ssentry, name), name
    assert len(k8ssentry.__all__) >= len(_GO_TOP_LEVEL)


def test_registry_maps_v1_pod_only():
    assert list(handlerRegistry) == [("v1", "Pod")]
    assert handlerRegistry[("v1", "Pod")] is NewPodEventHandler


def test_termination_key_is_a_value_type():
    a = terminationKey(podUID="u", containerName="c")
    b = terminationKey(podUID="u", containerName="c")
    assert a == b and hash(a) == hash(b)
    assert a.podUID == "u" and a.containerName == "c"


# --------------------------------------------------------------------------
# Handlers and registry dispatch
# --------------------------------------------------------------------------


def test_new_event_handler_falls_through_to_default():
    app = application()
    h = NewEventHandler(app, _event())
    assert isinstance(h, DefaultEventHandler)


def test_new_event_handler_uses_the_pod_factory_when_registered():
    """A Pod-involved event goes to the factory; a nil result falls through."""
    evt = _event(InvolvedObject=k8s.ObjectReference(APIVersion="v1", Kind="Pod", Name="p"))
    # No clientset -> the factory returns None -> default handler.
    assert isinstance(NewEventHandler(application(), evt), DefaultEventHandler)

    class _Client:
        def get_pod(self, ns, name, resource_version=""):
            return _pod(ObjectMeta_=k8s.ObjectMeta(Name="p", Namespace="d", UID="u"))

    h = NewEventHandler(application(clientset=_Client()), evt)
    assert isinstance(h, PodEventHandler)
    assert h.Fingerprint() == ["d", "u"]


def test_new_pod_event_handler_reports_api_failure_and_returns_none():
    class _Boom:
        def get_pod(self, ns, name, resource_version=""):
            raise RuntimeError("api down")

    hub = _Hub()
    app = application(clientset=_Boom(), hub=hub)
    assert NewPodEventHandler(app, _event()) is None
    assert len(hub.exceptions) == 1


def test_new_pod_event_handler_handles_a_missing_pod():
    class _Empty:
        def get_pod(self, ns, name, resource_version=""):
            return None

    assert NewPodEventHandler(application(clientset=_Empty()), _event()) is None


def test_pod_handler_tags_include_node_name():
    pod = _pod(
        ObjectMeta_=k8s.ObjectMeta(Labels={"app": "web"}),
        Spec=k8s.PodSpec(NodeName="node-9"),
    )
    tags = PodEventHandler(Pod=pod).Tags()
    assert tags == {"app": "web", "nodeName": "node-9"}
    assert PodEventHandler(Pod=_pod()).Tags() == {"nodeName": ""}


def test_fingerprint_from_meta_prefers_a_controlling_owner():
    meta = k8s.ObjectMeta(
        Namespace="ns",
        UID="uid",
        OwnerReferences=[
            k8s.OwnerReference(APIVersion="v1", Kind="Node", Name="n", Controller=False),
            k8s.OwnerReference(APIVersion="apps/v1", Kind="Deployment", Name="d", Controller=True),
        ],
    )
    assert fingerprintFromMeta(meta) == ["apps/v1", "Deployment", "d"]
    assert fingerprintFromMeta(k8s.ObjectMeta(Namespace="ns", UID="uid")) == ["ns", "uid"]


def test_default_handler_tags_are_gos_nil_map():
    """Go returns the nil map itself, which marshals to null rather than {}."""
    assert DefaultEventHandler(Event=_event()).Tags() is None
    evt = _event(ObjectMeta_=k8s.ObjectMeta(Labels={"a": "b"}))
    assert DefaultEventHandler(Event=evt).Tags() == {"a": "b"}


# --------------------------------------------------------------------------
# Capture paths and wrong-type guards
# --------------------------------------------------------------------------


def test_wrong_types_are_reported_not_raised():
    hub = _Hub()
    app = application(hub=hub)
    app.handlePodUpdate(None, "not a pod")
    app.handleEventAdd("not an event")
    assert hub.messages == ["Unexpected pod type", "Unexpected event type"]
    assert hub.events == []


def test_normal_events_are_skipped_entirely():
    hub = _Hub()
    application(hub=hub).handleEventAdd(_event(Type="Normal"))
    assert hub.events == []


def test_capture_helpers_are_noops_without_a_hub():
    app = application()
    app.capture_event(sentry.NewEvent())
    app.capture_message("m")
    app.capture_exception(RuntimeError("x"))


# --------------------------------------------------------------------------
# isNewTermination
# --------------------------------------------------------------------------


def _terminated_pod(ago_ms: int, name: str = "c"):
    pod = _pod(ObjectMeta_=k8s.ObjectMeta(UID="u"))
    st = k8s.ContainerStatus(
        Name=name,
        LastTerminationState=k8s.ContainerState(
            Terminated=k8s.ContainerStateTerminated(
                ExitCode=1,
                FinishedAt=k8s.Time(
                    _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(milliseconds=ago_ms)
                ),
            )
        ),
    )
    return pod, st


def test_is_new_termination_never_reports_a_first_sighting():
    """Upstream quirk, verified against Go: the FIRST termination is not reported.

    ``Get`` runs before ``Add``, so the cache is empty on first sight and Go's
    ``cachedTime.(metav1.Time)`` assertion fails, returning false. Only a LATER
    termination with a newer FinishedAt is ever reported. Recorded from Go:

        FIRST sighting         false
        SECOND same FinishedAt false
        THIRD newer FinishedAt true
    """
    app = application(terminationsSeen=lru.New(10))
    pod, st = _terminated_pod(0)
    assert app.isNewTermination(pod, st) is False
    assert app.isNewTermination(pod, st) is False

    pod2, st2 = _terminated_pod(0)
    assert app.isNewTermination(pod2, st2) is True


def test_is_new_termination_rejects_a_stale_record():
    app = application(terminationsSeen=lru.New(10))
    pod, st = _terminated_pod(5000)
    assert app.isNewTermination(pod, st) is False


def test_is_new_termination_reports_a_fresh_one_after_a_stale_one():
    """Recorded from Go: STALE first -> false, then fresh -> true."""
    app = application(terminationsSeen=lru.New(10))
    stale_pod, stale_st = _terminated_pod(10000)
    assert app.isNewTermination(stale_pod, stale_st) is False
    fresh_pod, fresh_st = _terminated_pod(0)
    assert app.isNewTermination(fresh_pod, fresh_st) is True


def test_is_new_termination_caches_even_when_it_rejects():
    """Go writes the cache before the age check, so a stale record still lands."""
    app = application(terminationsSeen=lru.New(10))
    pod, st = _terminated_pod(5000)
    app.isNewTermination(pod, st)
    key = terminationKey(podUID="u", containerName="c")
    assert app.terminationsSeen.Contains(key)


def test_is_new_termination_rejects_a_poisoned_cache_entry():
    cache = lru.New(10)
    cache.Add(terminationKey(podUID="u", containerName="c"), "not a Time")
    app = application(terminationsSeen=cache)
    pod, st = _terminated_pod(0)
    assert app.isNewTermination(pod, st) is False


# --------------------------------------------------------------------------
# LRU
# --------------------------------------------------------------------------


def test_lru_get_add_and_eviction():
    c = lru.New(2)
    assert c.Get("missing") == (None, False)
    assert c.Add("a", 1) is False
    assert c.Add("b", 2) is False
    assert c.Len() == 2
    assert c.Get("a") == (1, True)
    # "b" is now least-recent, so adding "c" evicts it.
    assert c.Add("c", 3) is True
    assert c.Contains("b") is False
    assert c.Contains("a") is True
    # Re-adding an existing key updates without evicting.
    assert c.Add("a", 9) is False
    assert c.Get("a") == (9, True)


def test_lru_rejects_a_non_positive_size():
    with pytest.raises(ValueError):
        lru.New(0)


# --------------------------------------------------------------------------
# Sentry model
# --------------------------------------------------------------------------


def test_bare_event_marshals_with_sdk_and_user():
    assert sentry.NewEvent().MarshalJSON() == '{"sdk":{},"user":{}}'


def test_sdk_and_user_render_their_fields():
    e = sentry.NewEvent()
    e.Sdk.Name = "sentry.go"
    e.Sdk.Version = "0.7.0"
    e.Sdk.Integrations = ["Modules"]
    e.Sdk.Packages = [{"name": "sentry-go"}]
    e.User.Email = "a@b.c"
    e.User.ID = "1"
    e.User.IPAddress = "127.0.0.1"
    e.User.Username = "u"
    out = e.MarshalJSON()
    assert '"sdk":{"name":"sentry.go","version":"0.7.0","integrations":["Modules"]' in out
    assert '"user":{"email":"a@b.c","id":"1","ip_address":"127.0.0.1","username":"u"}' in out


def test_optional_fields_appear_only_when_set():
    e = sentry.NewEvent()
    e.Dist = "d"
    e.EventID = "id"
    e.Platform = "go"
    e.Transaction = "t"
    e.Modules = {"m": "1"}
    e.Contexts = {"c": {"k": 1}}
    e.Breadcrumbs = [{"b": 1}]
    e.Threads = [{"t": 1}]
    e.Exception = [{"e": 1}]
    e.Request = {"url": "u"}
    out = e.MarshalJSON()
    for key in (
        "dist",
        "event_id",
        "platform",
        "transaction",
        "modules",
        "contexts",
        "breadcrumbs",
        "threads",
        "exception",
        "request",
    ):
        assert '"{}"'.format(key) in out, key


def test_zero_timestamp_is_omitted():
    e = sentry.NewEvent()
    e.Timestamp = _dt.datetime(1, 1, 1, tzinfo=_dt.timezone.utc)
    assert "timestamp" not in e.MarshalJSON()


# --------------------------------------------------------------------------
# Go JSON semantics
# --------------------------------------------------------------------------


def test_encode_value_covers_every_kind():
    assert gojson.encode_value(None) == "null"
    assert gojson.encode_value(True) == "true"
    assert gojson.encode_value(False) == "false"
    assert gojson.encode_value(7) == "7"
    assert gojson.encode_value(3.5) == "3.5"
    assert gojson.encode_value("s") == '"s"'
    assert gojson.encode_value([1, "a"]) == '[1,"a"]'
    assert gojson.encode_value({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    # HTML escaping applies to the fallback rendering too.
    assert gojson.encode_value(object()).startswith('"\\u003cobject')


def test_html_escaping_and_control_characters():
    assert gojson.encode_string("<a>&b") == '"\\u003ca\\u003e\\u0026b"'
    assert gojson.encode_string("\n\t\r") == '"\\n\\t\\r"'
    assert gojson.encode_string("\x00") == '"\\u0000"'
    assert gojson.encode_string("\u2028") == '"\\u2028"'
    assert gojson.encode_string("héllo") == '"héllo"'


def test_invalid_utf8_becomes_one_replacement_per_byte():
    bad = b"a\x80b".decode("utf-8", "surrogateescape")
    assert gojson.encode_string(bad) == '"a\\ufffdb"'


def test_float_encoding_matches_go():
    assert gojson.encode_value(0.0) == "0"
    assert gojson.encode_value(-0.0) == "-0"
    assert gojson.encode_value(1e-7) == "1e-7"
    assert gojson.encode_value(1e21) == "1e+21"
    assert gojson.encode_value(1000000.0) == "1000000"
    with pytest.raises(ValueError):
        gojson.encode_value(float("nan"))


def test_is_empty_has_no_case_for_structs():
    assert gojson.is_empty(None) is True
    assert gojson.is_empty(False) is True
    assert gojson.is_empty(0) is True
    assert gojson.is_empty("") is True
    assert gojson.is_empty([]) is True
    assert gojson.is_empty({}) is True
    assert gojson.is_empty("x") is False
    assert gojson.is_empty(sentry.SdkInfo()) is False


def test_rfc3339_trims_trailing_zeros_and_converts_to_utc():
    t = _dt.datetime(2020, 10, 5, 13, 39, 18, tzinfo=_dt.timezone(_dt.timedelta(hours=2)))
    assert gojson.format_rfc3339_nano(t) == "2020-10-05T11:39:18Z"
    t2 = _dt.datetime(2020, 10, 5, 11, 39, 18, 123000, tzinfo=_dt.timezone.utc)
    assert gojson.format_rfc3339_nano(t2) == "2020-10-05T11:39:18.123Z"


def test_go_time_carries_nanoseconds():
    t = gojson.GoTime(2020, 10, 5, 11, 39, 18, 123456, tzinfo=_dt.timezone.utc)
    t._go_nanos = 123456789
    assert gojson.format_rfc3339_nano(t) == "2020-10-05T11:39:18.123456789Z"


# --------------------------------------------------------------------------
# k8s models
# --------------------------------------------------------------------------


def test_metadata_promotion_matches_gos_embedding():
    meta = k8s.ObjectMeta(Name="n", Namespace="ns", UID="u", ClusterName="c", Labels={"a": "b"})
    evt = k8s.Event(ObjectMeta_=meta)
    assert (evt.Name, evt.Namespace, evt.UID, evt.ClusterName) == ("n", "ns", "u", "c")
    assert evt.Labels == {"a": "b"}
    assert evt.GetLabels() == {"a": "b"}


def test_time_helpers():
    assert k8s.Time().IsZero() is True
    now = k8s.now()
    assert now.IsZero() is False
    earlier = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=1)
    assert now.After(earlier) is True
    assert now.Sub(earlier).total_seconds() > 0


# --------------------------------------------------------------------------
# Transport adapter
# --------------------------------------------------------------------------


def test_sentry_hub_collects_events_without_a_dsn():
    hub = transport.SentryHub()
    e = sentry.NewEvent()
    e.Message = "m"
    hub.CaptureEvent(e)
    hub.CaptureMessage("msg")
    hub.CaptureException(RuntimeError("boom"))
    hub.Flush(0.1)
    assert len(hub.events) == 3
    assert hub.events[1].Message == "msg"
    assert hub.events[2].Level == sentry.LevelError
    # The hub substitutes a hostname exactly where Go's SDK does.
    assert hub.events[0].ServerName != ""


def test_sdk_payload_shape():
    e = sentry.NewEvent()
    e.Message = "m"
    e.Release = "r"
    e.Timestamp = _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)
    payload = transport._to_sdk_payload(e)
    assert payload["message"] == "m"
    assert payload["level"] == "info"
    assert payload["release"] == "r"
    assert payload["timestamp"] is e.Timestamp


class _FakeMeta:
    def __init__(self):
        self.name = "p"
        self.namespace = "ns"
        self.uid = "u"
        self.cluster_name = "c"
        self.labels = {"a": "b"}
        self.owner_references = [
            type(
                "O",
                (),
                {"api_version": "apps/v1", "kind": "ReplicaSet", "name": "rs", "controller": True},
            )()
        ]
        self.creation_timestamp = _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)


def test_pod_and_event_translation_from_the_official_client():
    term = type(
        "T",
        (),
        {
            "exit_code": 137,
            "reason": "OOMKilled",
            "message": "",
            "finished_at": _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc),
        },
    )()
    cs = type(
        "CS",
        (),
        {
            "name": "web",
            "image": "img",
            "restart_count": 2,
            "last_state": type("L", (), {"terminated": term})(),
        },
    )()
    raw_pod = type(
        "P",
        (),
        {
            "metadata": _FakeMeta(),
            "kind": "Pod",
            "spec": type("S", (), {"node_name": "n1"})(),
            "status": type(
                "St",
                (),
                {
                    "phase": "Running",
                    "message": "",
                    "reason": "",
                    "container_statuses": [cs],
                    "init_container_statuses": [],
                },
            )(),
        },
    )()
    pod = transport.pod_from_api(raw_pod)
    assert pod.Name == "p" and pod.Spec.NodeName == "n1"
    assert pod.Status.ContainerStatuses[0].LastTerminationState.Terminated.ExitCode == 137
    assert fingerprintFromMeta(pod.ObjectMeta) == ["apps/v1", "ReplicaSet", "rs"]

    raw_evt = type(
        "E",
        (),
        {
            "metadata": _FakeMeta(),
            "type": "Warning",
            "reason": "BackOff",
            "message": "m",
            "action": "",
            "count": 3,
            "source": type("Src", (), {"component": "kubelet"})(),
            "involved_object": type(
                "IO",
                (),
                {
                    "api_version": "v1",
                    "kind": "Node",
                    "namespace": "ns",
                    "name": "n",
                    "field_path": "",
                    "resource_version": "7",
                },
            )(),
        },
    )()
    evt = transport.event_from_api(raw_evt)
    assert evt.Type == "Warning" and evt.Count == 3
    assert evt.InvolvedObject.Kind == "Node"
    assert evt.Source.Component == "kubelet"


def test_translation_tolerates_missing_metadata():
    bare = type("X", (), {})()
    assert transport.pod_from_api(bare).Name == ""
    assert transport.event_from_api(bare).Type == ""


def test_kubernetes_client_without_a_backend_is_inert():
    c = transport.KubernetesClient()
    assert c.get_pod("ns", "n") is None
    assert list(c.watch("pods", "ns")) == []


def test_in_cluster_config_follows_the_env():
    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ.pop("KUBERNETES_SERVICE_PORT", None)
    assert transport.inClusterConfig() is False


def test_create_kubernetes_client_returns_an_error_pair():
    client, err = transport.createKubernetesClient("/nonexistent/kubeconfig")
    assert client is None
    assert err is not None


def test_dispatch_unwraps_watch_items():
    seen = []
    transport.dispatch([{"object": 1}, 2], seen.append)
    assert seen == [1, 2]


# --------------------------------------------------------------------------
# Daemon wiring
# --------------------------------------------------------------------------


def test_run_starts_a_watcher_per_namespace():
    seen = []

    class _Client:
        def watch(self, resource, namespace):
            seen.append((resource, namespace))
            return iter(())

    app = application(clientset=_Client(), namespaces=["a", "b"])
    stop, err = app.Run()
    assert err is None
    assert isinstance(stop, threading.Event)
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=1)
    assert app.terminationsSeen is not None
    assert sorted(seen) == [("events", "a"), ("events", "b"), ("pods", "a"), ("pods", "b")]


def test_monitor_is_inert_without_a_watchable_client():
    app = application(clientset=object())
    app.monitorPods("ns", threading.Event())
    app.monitorEvents("ns", threading.Event())


def test_monitor_dispatches_and_honours_stop():
    hub = _Hub()
    pod = _pod(
        ObjectMeta_=k8s.ObjectMeta(Namespace="d", UID="u"),
        Status=k8s.PodStatus(Phase="Failed", Message="m", Reason="r"),
    )

    class _Client:
        def watch(self, resource, namespace):
            return iter([{"object": pod}])

    app = application(clientset=_Client(), terminationsSeen=lru.New(5), hub=hub)
    app.monitorPods("ns", threading.Event())
    assert len(hub.events) == 1
    assert hub.events[0].Message == "Pod/: m"

    stopped = threading.Event()
    stopped.set()
    hub.events.clear()
    app.monitorPods("ns", stopped)
    assert hub.events == []


def test_monitor_events_dispatches_event_objects():
    hub = _Hub()
    evt = _event(Type="Warning", Message="m")

    class _Client:
        def watch(self, resource, namespace):
            return iter([evt])

    application(clientset=_Client(), hub=hub).monitorEvents("ns", threading.Event())
    assert len(hub.events) == 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_parse_args_defaults_and_flag():
    assert cli.parse_args([]).kubeconfig == ""
    assert cli.parse_args(["--kubeconfig", "/tmp/k"]).kubeconfig == "/tmp/k"


def test_resolve_environment_prefers_sentry_environment():
    os.environ.pop("SENTRY_ENVIRONMENT", None)
    os.environ.pop("ENVIRONMENT", None)
    assert cli.resolve_environment(lambda *_: None) == ""

    os.environ["ENVIRONMENT"] = "legacy"
    warnings = []
    assert cli.resolve_environment(warnings.append) == "legacy"
    assert len(warnings) == 1 and "deprecated" in warnings[0]

    os.environ["SENTRY_ENVIRONMENT"] = "prod"
    warnings.clear()
    assert cli.resolve_environment(warnings.append) == "prod"
    assert warnings == []
    os.environ.pop("SENTRY_ENVIRONMENT", None)
    os.environ.pop("ENVIRONMENT", None)


def test_resolve_namespaces():
    os.environ.pop("NAMESPACE", None)
    assert cli.resolve_namespaces() == [k8s.NamespaceAll]
    os.environ["NAMESPACE"] = "a,b"
    assert cli.resolve_namespaces() == ["a", "b"]
    os.environ.pop("NAMESPACE", None)


def test_main_reports_a_client_failure_and_exits_nonzero():
    logs = []
    os.environ.pop("SENTRY_DSN", None)
    rc = cli.main(["--kubeconfig", "/nonexistent/kubeconfig"], log=logs.append)
    assert rc == 1
    assert any("SENTRY_DSN" in m for m in logs)
    assert any("Error creating kubernetes client" in m for m in logs)


def test_module_entrypoint_is_wired():
    assert callable(cli.main)
    assert io is not None


def test_signal_handlers_set_the_abort_event():
    """Port of signal.Notify: SIGTERM/SIGINT/SIGHUP must release the wait."""
    import signal as _signal

    abort = cli.install_signal_handlers()
    assert isinstance(abort, threading.Event)
    assert not abort.is_set()

    handler = _signal.getsignal(_signal.SIGTERM)
    assert callable(handler)
    handler(_signal.SIGTERM, None)
    assert abort.is_set()

    _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)
    _signal.signal(_signal.SIGINT, _signal.default_int_handler)


def test_sentry_sdk_init_is_optional():
    """Without sentry_sdk installed the hub degrades to in-memory collection."""
    assert transport.SentryHub._init_sdk("https://x@y/1", "prod") is None
    hub = transport.SentryHub(dsn="https://x@y/1", environment="prod")
    assert hub._client is None
    hub.CaptureMessage("m")
    assert len(hub.events) == 1
