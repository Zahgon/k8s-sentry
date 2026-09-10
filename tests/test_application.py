"""1:1 port of ``application_test.go`` -- the Go program's entire test suite.

Go declares three test functions; this file declares the same three, with the
same names and the same assertions in the same order.
"""

from __future__ import annotations

import os

from k8ssentry import _k8s as k8s
from k8ssentry import _sentry as sentry
from k8ssentry.application import getSentryLevel, inCluster, skipEvent


def test_skip_event() -> None:
    """Port of ``TestSkipEvent``."""
    evt = k8s.Event(Type=k8s.EventTypeNormal)
    assert skipEvent(evt), "Normal events must be skipped"

    evt.Type = k8s.EventTypeWarning
    assert not skipEvent(evt), "Warnings events must not be skipped"

    evt.Type = "Error"
    assert not skipEvent(evt), "Error events must not be skipped"

    evt.Type = "Unknown"
    assert not skipEvent(evt), "Unknown event types must not be skipped"


def test_get_sentry_level() -> None:
    """Port of ``TestGetSentryLevel``."""
    evt = k8s.Event(Type="Warning")
    assert getSentryLevel(evt) == sentry.LevelWarning, (
        "Type Warning not reported with warning level"
    )

    evt.Type = "Error"
    assert getSentryLevel(evt) == sentry.LevelError, "Type Error not reported with error level"

    evt.Type = "Other"
    assert getSentryLevel(evt) == sentry.LevelInfo, (
        "Unknown event types not reported with info level"
    )


def test_in_cluster() -> None:
    """Port of ``TestInCluster``."""
    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ.pop("KUBERNETES_SERVICE_PORT", None)

    assert not inCluster(), "inCluster returns true if Kubernetes service env is missing"

    os.environ["KUBERNETES_SERVICE_HOST"] = "api"
    assert not inCluster(), "inCluster returns true if KUBERNETES_SERVICE_PORT is missing"

    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ["KUBERNETES_SERVICE_PORT"] = "4138"
    assert not inCluster(), "inCluster returns true if KUBERNETES_SERVICE_HOST is missing"

    os.environ["KUBERNETES_SERVICE_HOST"] = "api"
    os.environ["KUBERNETES_SERVICE_PORT"] = "4138"
    assert inCluster(), "inCluster returns false with Kubernetes service env present"

    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ.pop("KUBERNETES_SERVICE_PORT", None)
