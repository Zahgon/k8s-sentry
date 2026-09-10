"""The corpus generator must produce a complete, well-formed input set.

``truth_inputs.py`` is what both sides are driven with, so a silently shrinking
or malformed corpus would weaken the differential without failing it. These
checks run the generator and assert its shape.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "verification"))

import truth_inputs  # noqa: E402

_EXPECTED_KEYS = {
    "event_types",
    "env_pairs",
    "metas",
    "events",
    "pods",
    "sentry_raw",
    "pod_seqs",
}


def test_corpus_has_every_input_class():
    data = truth_inputs.build()
    assert set(data) == _EXPECTED_KEYS
    for key, values in data.items():
        assert len(values) > 0, key


def test_corpus_covers_the_event_type_branches():
    types = truth_inputs.build()["event_types"]
    # The three branches of getSentryLevel, plus casing that must NOT match.
    for required in ("Normal", "Warning", "Error", "warning", "error", ""):
        assert required in types, required


def test_corpus_covers_every_controller_owner_shape():
    metas = truth_inputs.build()["metas"]
    controllers = [o["controller"] for m in metas for o in (m["owners"] or [])]
    assert True in controllers
    assert False in controllers
    assert None in controllers, "an owner with no controller flag must be covered"
    assert any(not m["owners"] for m in metas), "the no-owner path must be covered"


def test_corpus_reaches_the_terminated_branch():
    """A single update can never report, so sequences sharing an LRU are needed."""
    seqs = truth_inputs.build()["pod_seqs"]
    assert len(seqs) > 0
    assert all(len(seq) >= 2 for seq in seqs), "a sequence of one proves nothing"
    # At least one sequence must present a strictly NEWER FinishedAt second.
    assert any(
        seq[1]["containers"][0]["finished_ago_ms"] < seq[0]["containers"][0]["finished_ago_ms"]
        for seq in seqs
    ), "no sequence gets newer, so the report path stays unreachable"


def test_corpus_covers_both_pod_branches():
    pods = truth_inputs.build()["pods"]
    assert any(p["phase"] == "Failed" for p in pods), "PodFailed branch"
    assert any(c["terminated"] and c["exit_code"] != 0 for p in pods for c in p["containers"]), (
        "terminated-container branch"
    )
    assert any(c["init"] for p in pods for c in p["containers"]), "init containers"
    # Go's window is age.Microseconds() > 5000, i.e. 5 MILLIseconds. The corpus
    # field is in milliseconds, so anything above 5 lands outside it.
    assert any(c["finished_ago_ms"] > 5 for p in pods for c in p["containers"]), (
        "the stale-termination window"
    )
    assert any(
        c["terminated"] and c["finished_ago_ms"] == 0 for p in pods for c in p["containers"]
    ), "a fresh termination"


def test_corpus_covers_timestamp_and_escaping_cases():
    raw = truth_inputs.build()["sentry_raw"]
    assert any(s["timestamp"] and s["timestamp"].endswith("Z") for s in raw)
    assert any(s["timestamp"] and "+" in s["timestamp"] for s in raw), "non-UTC offset"
    assert any("<" in (s["message"] or "") for s in raw), "HTML escaping"
    assert any(s["fingerprint"] == [] for s in raw), "empty slice is omitted"


def test_generator_writes_the_input_file(tmp_path, monkeypatch):
    monkeypatch.setattr(truth_inputs.os.path, "dirname", lambda _p: str(tmp_path))
    truth_inputs.main()
    written = tmp_path / "truth-inputs.json"
    assert written.exists()
    assert written.stat().st_size > 0
