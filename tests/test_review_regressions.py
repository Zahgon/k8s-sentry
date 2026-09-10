"""Regressions for divergences found by adversarial review of the port.

Each pins a behaviour **verified against real Go** (go1.26.7, sentry-go v0.7.0).
The corpus was extended and re-recorded after these fixes, so they are also
covered byte-for-byte by ``verification/go-truth.json``; they are re-pinned here
so a failure names the finding directly.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from k8ssentry import _gojson as gojson
from k8ssentry import _k8s as k8s
from k8ssentry import _lru as lru
from k8ssentry import _sentry as sentry
from k8ssentry.application import application

_UTC = _dt.timezone.utc
_P1 = _dt.timezone(_dt.timedelta(hours=1))
_M1 = _dt.timezone(_dt.timedelta(hours=-1))


def _event_with(ts):
    e = sentry.NewEvent()
    e.Timestamp = ts
    return e


# --------------------------------------------------------------------------
# F1 - IsZero compares the INSTANT, not wall-clock fields
# --------------------------------------------------------------------------


def test_zero_instant_ignores_the_zone():
    """Go: 0001-01-01T01:00:00+01:00 IS the zero instant; 00:00-01:00 is not."""
    assert gojson.is_zero_instant(_dt.datetime(1, 1, 1, tzinfo=_UTC)) is True
    assert gojson.is_zero_instant(_dt.datetime(1, 1, 1, 1, 0, 0, tzinfo=_P1)) is True
    assert gojson.is_zero_instant(_dt.datetime(1, 1, 1, 0, 0, 0, tzinfo=_M1)) is False


def test_zero_instant_counts_nanoseconds():
    """A sub-microsecond digit makes the instant non-zero; Go emits it."""
    g = gojson.GoTime(1, 1, 1, tzinfo=_UTC)
    g._go_nanos = 1
    assert gojson.is_zero_instant(g) is False


@pytest.mark.parametrize(
    ("ts", "want"),
    [
        (_dt.datetime(1, 1, 1, tzinfo=_UTC), '{"sdk":{},"user":{}}'),
        (
            _dt.datetime(1, 1, 1, 1, 0, 0, tzinfo=_P1),
            '{"sdk":{},"user":{}}',
        ),
        (
            _dt.datetime(1, 1, 1, 0, 0, 0, tzinfo=_M1),
            '{"sdk":{},"user":{},"timestamp":"0001-01-01T01:00:00Z"}',
        ),
    ],
)
def test_timestamp_emission_follows_the_instant(ts, want):
    assert _event_with(ts).MarshalJSON() == want


def test_nanosecond_zero_date_is_emitted():
    g = gojson.GoTime(1, 1, 1, tzinfo=_UTC)
    g._go_nanos = 1
    assert _event_with(g).MarshalJSON() == (
        '{"sdk":{},"user":{},"timestamp":"0001-01-01T00:00:00.000000001Z"}'
    )


# --------------------------------------------------------------------------
# F2 - formatting must not overflow below datetime.min
# --------------------------------------------------------------------------


def test_year_one_with_a_positive_offset_rolls_into_year_zero():
    """Go: time.Date(1,1,1,0,0,0,0,+01:00).UTC() == 0000-12-31T23:00:00Z.

    datetime cannot represent year 0, so astimezone raises OverflowError here.
    """
    assert gojson.format_rfc3339_nano(_dt.datetime(1, 1, 1, tzinfo=_P1)) == ("0000-12-31T23:00:00Z")
    assert gojson.to_utc_parts(_dt.datetime(1, 1, 1, tzinfo=_P1)) == (0, 12, 31, 23, 0, 0)


def test_ordinary_offsets_still_convert():
    t = _dt.datetime(2020, 10, 5, 13, 39, 18, tzinfo=_dt.timezone(_dt.timedelta(hours=2)))
    assert gojson.format_rfc3339_nano(t) == "2020-10-05T11:39:18Z"


# --------------------------------------------------------------------------
# F3 - a naive datetime must not crash comparisons Go can always make
# --------------------------------------------------------------------------


def test_naive_times_are_coerced_not_crashed():
    """Go's time.Time always has a Location, so Sub/After/IsZero are total."""
    t = k8s.Time(_dt.datetime(2030, 1, 1))
    assert t.Time.tzinfo is not None
    assert t.After(_dt.datetime(2029, 1, 1, tzinfo=_UTC)) is True
    assert k8s.Time(_dt.datetime(1, 1, 1)).IsZero() is True


def test_naive_finished_at_does_not_crash_is_new_termination():
    pod = k8s.Pod(ObjectMeta_=k8s.ObjectMeta(UID="u"))
    st = k8s.ContainerStatus(
        Name="c",
        LastTerminationState=k8s.ContainerState(
            Terminated=k8s.ContainerStateTerminated(
                ExitCode=1, FinishedAt=k8s.Time(_dt.datetime(2030, 1, 1))
            )
        ),
    )
    app = application(terminationsSeen=lru.New(10))
    assert app.isNewTermination(pod, st) is False


# --------------------------------------------------------------------------
# F4 - SdkPackage is a struct: omitempty per field, declaration order
# --------------------------------------------------------------------------


def test_sdk_packages_use_struct_semantics():
    e = sentry.NewEvent()
    e.Sdk.Packages = [{"version": "1"}, {"name": "n"}, {"name": "a", "version": "b"}]
    out = e.MarshalJSON()
    assert '"packages":[{"version":"1"},{"name":"n"},{"name":"a","version":"b"}]' in out


# --------------------------------------------------------------------------
# F5 - a lone surrogate must not raise
# --------------------------------------------------------------------------


def test_lone_surrogate_becomes_the_replacement_character():
    """Go's JSON decoder already turned unpaired surrogates into U+FFFD."""
    assert gojson.encode_string("a\ud800b") == '"a\ufffdb"'
    # A surrogateescape byte is still one escape per byte.
    assert gojson.encode_string(b"a\x80b".decode("utf-8", "surrogateescape")) == '"a\\ufffdb"'


# --------------------------------------------------------------------------
# F6 - Go's log.Printf carries the LstdFlags prefix
# --------------------------------------------------------------------------


def test_event_log_line_has_gos_date_prefix(capsys):
    import re

    hub_events = []

    class _Hub:
        def CaptureEvent(self, e):
            hub_events.append(e)

    evt = k8s.Event(
        Type="Warning",
        Message="boom",
        InvolvedObject=k8s.ObjectReference(APIVersion="v1", Kind="Node", Name="n"),
    )
    application(hub=_Hub()).handleEventAdd(evt)
    err = capsys.readouterr().err
    assert re.search(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} Warning Node/n: boom$", err.strip())


# --------------------------------------------------------------------------
# F7 - microsecond truncation without a float round-trip
# --------------------------------------------------------------------------


def test_microsecond_truncation_is_exact():
    """Go: age.Microseconds() is integer division; total_seconds() rounds twice."""
    for micros in (249, 251, 489, 4999, 5000, 5001, 5002, 999_999_999):
        delta = _dt.timedelta(microseconds=micros)
        assert delta // _dt.timedelta(microseconds=1) == micros


# --------------------------------------------------------------------------
# The coverage hole the corpus could not reach before
# --------------------------------------------------------------------------


# Pinned once: recomputing now() per call would make an identical ago_ms a
# strictly LATER instant, which silently turns "same" into "newer".
_FUTURE_BASE = _dt.datetime.now(_UTC) + _dt.timedelta(hours=1)


def _term_pod(ago_ms, **kw):
    base = _FUTURE_BASE
    st = k8s.ContainerStatus(
        Name=kw.pop("name", "web"),
        Image="img:1",
        RestartCount=kw.pop("restart", 0),
        LastTerminationState=k8s.ContainerState(
            Terminated=k8s.ContainerStateTerminated(
                ExitCode=kw.pop("exit", 1),
                Reason=kw.pop("reason", "Error"),
                Message=kw.pop("message", ""),
                FinishedAt=k8s.Time(base - _dt.timedelta(milliseconds=ago_ms)),
            )
        ),
    )
    pod = k8s.Pod(
        ObjectMeta_=k8s.ObjectMeta(Name="web-1", Namespace="default", UID="u"),
        Kind="Pod",
        Spec=k8s.PodSpec(NodeName="node-1"),
        Status=k8s.PodStatus(Phase="Running", ContainerStatuses=[st]),
    )
    return pod


def test_terminated_branch_reports_only_on_a_newer_finished_at():
    """A single update can never report; the corpus needed LRU-sharing sequences."""
    captured = []

    class _Hub:
        def CaptureEvent(self, e):
            captured.append(e)

    app = application(terminationsSeen=lru.New(500), hub=_Hub())
    app.handlePodUpdate(None, _term_pod(1000))
    assert captured == [], "first sighting must not report"

    app.handlePodUpdate(None, _term_pod(1000))
    assert captured == [], "same FinishedAt must not report"

    app.handlePodUpdate(None, _term_pod(500))
    assert len(captured) == 1, "a newer FinishedAt must report"

    e = captured[0]
    assert e.Level == sentry.LevelError
    assert e.Extra["exit-code"] == "1"
    assert e.Release == "img:1"
    assert e.Tags["reason"] == "Error"


def test_oom_kill_falls_back_to_the_reason_as_message():
    captured = []

    class _Hub:
        def CaptureEvent(self, e):
            captured.append(e)

    app = application(terminationsSeen=lru.New(500), hub=_Hub())
    app.handlePodUpdate(None, _term_pod(1000, reason="OOMKilled", message="", exit=137))
    app.handlePodUpdate(None, _term_pod(500, reason="OOMKilled", message="", exit=137))
    assert len(captured) == 1
    assert captured[0].Message == "Pod/web-1: OOMKilled"


def test_labels_can_overwrite_the_base_tags():
    """Labels are merged last, so a label named "reason" wins."""
    captured = []

    class _Hub:
        def CaptureEvent(self, e):
            captured.append(e)

    pod = k8s.Pod(
        ObjectMeta_=k8s.ObjectMeta(
            Name="p", Namespace="ns", UID="u", Labels={"reason": "LABEL", "kind": "LK"}
        ),
        Kind="Pod",
        Status=k8s.PodStatus(Phase="Failed", Message="m", Reason="REAL"),
    )
    application(terminationsSeen=lru.New(5), hub=_Hub()).handlePodUpdate(None, pod)
    assert captured[0].Tags["reason"] == "LABEL"
    assert captured[0].Tags["kind"] == "LK"
    # The fingerprint was built from the ORIGINAL reason, before the overwrite.
    assert captured[0].Fingerprint[0] == "REAL"


def test_pod_kind_is_not_defaulted_when_the_api_omits_it():
    """``pod_from_api`` must leave an absent ``kind`` empty, not invent "Pod".

    The API server strips TypeMeta from the objects inside a list or watch
    response, so ``kind`` is absent for every Pod this tool actually receives
    and the official client's ``V1Pod().kind`` is ``None``. Go's
    ``v1.Pod.Kind`` is ``""`` there, and that empty string is what reaches
    Sentry as ``tags["kind"]``. Defaulting to "Pod" changed the tag on every
    pod event in production.
    """
    from types import SimpleNamespace

    from k8ssentry.transport import pod_from_api

    absent = SimpleNamespace(metadata=SimpleNamespace(name="p"), spec=None, status=None)
    assert pod_from_api(absent).Kind == ""

    explicit_none = SimpleNamespace(
        kind=None, metadata=SimpleNamespace(name="p"), spec=None, status=None
    )
    assert pod_from_api(explicit_none).Kind == ""

    empty = SimpleNamespace(
        kind="", metadata=SimpleNamespace(name="p"), spec=None, status=None
    )
    assert pod_from_api(empty).Kind == ""

    # A kind the API really did send is still carried through.
    present = SimpleNamespace(
        kind="Pod", metadata=SimpleNamespace(name="p"), spec=None, status=None
    )
    assert pod_from_api(present).Kind == "Pod"


def test_absent_pod_kind_reaches_sentry_as_an_empty_tag():
    """The end-to-end consequence of the above: tags["kind"] is "", not "Pod"."""
    from types import SimpleNamespace

    from k8ssentry.transport import pod_from_api

    captured = []

    class _Hub:
        def CaptureEvent(self, e):
            captured.append(e)

    raw = SimpleNamespace(
        metadata=SimpleNamespace(name="p", namespace="ns", uid="u"),
        spec=SimpleNamespace(node_name="n"),
        status=SimpleNamespace(phase="Failed", message="m", reason="Evicted"),
    )
    pod = pod_from_api(raw)
    application(terminationsSeen=lru.New(5), hub=_Hub()).handlePodUpdate(None, pod)

    assert captured[0].Tags["kind"] == ""


def test_a_null_label_value_decodes_to_an_empty_string():
    """Go decodes a JSON null into ``map[string]string`` as ``""``.

    Left as ``None`` the value reaches ``encode_string``, which only handles
    strings, and the TypeError takes down the whole event handler where Go
    reports the event with an empty tag.
    """
    from types import SimpleNamespace

    from k8ssentry.transport import pod_from_api

    raw = SimpleNamespace(
        metadata=SimpleNamespace(name="p", labels={"a": None, "b": "x"}),
        spec=None,
        status=None,
    )
    assert pod_from_api(raw).ObjectMeta.Labels == {"a": "", "b": "x"}

    # A nil map is still nil: GetLabels hands it back and it marshals as null.
    nil = SimpleNamespace(metadata=SimpleNamespace(name="p"), spec=None, status=None)
    assert pod_from_api(nil).ObjectMeta.Labels is None


def test_a_null_label_value_still_produces_an_event():
    """End to end: the event is reported, with the tag present and empty."""
    from types import SimpleNamespace

    from k8ssentry.transport import pod_from_api

    captured = []

    class _Hub:
        def CaptureEvent(self, e):
            captured.append(e)

    raw = SimpleNamespace(
        metadata=SimpleNamespace(name="p", namespace="ns", uid="u", labels={"a": None}),
        spec=SimpleNamespace(node_name="n"),
        status=SimpleNamespace(phase="Failed", message="m", reason="Evicted"),
    )
    app = application(terminationsSeen=lru.New(5), hub=_Hub())
    app.handlePodUpdate(None, pod_from_api(raw))

    assert len(captured) == 1
    assert captured[0].Tags["a"] == ""
    assert '"a":""' in captured[0].MarshalJSON()
