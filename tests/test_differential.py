"""The behavioural gate: every recorded Go value replayed through the port.

``verification/go-truth.json`` is recorded by executing the real
``wichert/k8s-sentry`` (see ``verification/gen-go-truth.sh``, which copies its
probe into a throwaway copy of the checkout and fails if ``git status`` there
changes). This test replays the identical corpus and requires **zero**
divergences.

Observables per kind:

* ``eventtype``       -- ``skipEvent`` and ``getSentryLevel`` together
* ``incluster``       -- ``inCluster`` over env combinations
* ``fingerprintmeta`` -- ``fingerprintFromMeta``, incl. the controller-owner rules
* ``defaulthandler``  -- ``DefaultEventHandler.Fingerprint()``/``Tags()``
* ``podhandler``      -- ``PodEventHandler.Fingerprint()``/``Tags()``
* ``eventadd``        -- the **whole Sentry payload** built from a ``v1.Event``
* ``podupdate``       -- the **whole Sentry payload** built from a ``v1.Pod``
* ``podseq``          -- sequences sharing one LRU, the only way to reach the
  terminated-container branch
* ``sentryjson``      -- ``Event.MarshalJSON`` on its own
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_VERIFICATION = os.path.join(os.path.dirname(_HERE), "verification")
sys.path.insert(0, _VERIFICATION)

_KINDS = (
    "eventtype",
    "incluster",
    "fingerprintmeta",
    "defaulthandler",
    "eventadd",
    "podhandler",
    "podupdate",
    "podseq",
    "sentryjson",
)


def _load(name):
    path = os.path.join(_VERIFICATION, name)
    if not os.path.exists(path):
        pytest.skip("{} not present; run verification/gen-go-truth.sh".format(name))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_zero_divergences_against_recorded_go(capsys):
    from py_truth import run

    inputs = _load("truth-inputs.json")
    recorded = _load("go-truth.json")
    got = run(inputs)
    # The port prints exactly as the Go original does; keep it out of the report.
    capsys.readouterr()

    assert len(recorded) == len(got)
    assert len(recorded) >= 100

    by_kind = {}
    counts = {}
    observables = 0
    for want, have in zip(recorded, got):
        assert want["kind"] == have["kind"]
        kind = want["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        for key in ("out", "err"):
            observables += 1
            if want[key] != have[key]:
                by_kind.setdefault(kind, []).append(
                    "{} {}: in={!r}\n    go={!r}\n    py={!r}".format(
                        kind, key, want["in"], want[key], have[key]
                    )
                )

    def report(kind):
        return "{}/{} {} case(s) diverge:\n{}".format(
            len(by_kind.get(kind, [])),
            counts.get(kind, 0),
            kind,
            "\n".join(by_kind.get(kind, [])[:10]),
        )

    # Asserted per kind so a failure names the observable class immediately.
    assert "eventtype" not in by_kind, report("eventtype")
    assert "incluster" not in by_kind, report("incluster")
    assert "fingerprintmeta" not in by_kind, report("fingerprintmeta")
    assert "defaulthandler" not in by_kind, report("defaulthandler")
    assert "podhandler" not in by_kind, report("podhandler")
    assert "eventadd" not in by_kind, report("eventadd")
    assert "podupdate" not in by_kind, report("podupdate")
    assert "podseq" not in by_kind, report("podseq")
    assert "sentryjson" not in by_kind, report("sentryjson")
    assert not by_kind, report(next(iter(by_kind)))

    # Guards against a silently shrinking corpus: every kind must be present.
    assert set(counts) == set(_KINDS)
    print(
        "\nRESULT: 0 divergences across {} cases x {} observables {}".format(
            len(recorded), observables, sorted(counts.items())
        )
    )
