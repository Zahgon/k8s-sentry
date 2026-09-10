"""Port of ``main.go``.

The wiring is I/O and sits outside the byte-exact claim (see
``verification/SCOPE.md``); the argument handling, environment precedence and
warning messages are still ported faithfully because they are user-visible.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from types import FrameType
from typing import Any, Callable, List, Optional, Sequence

from . import _k8s as k8s
from .application import application
from .transport import SentryHub, createKubernetesClient

__all__ = [
    "main",
    "parse_args",
    "resolve_environment",
    "resolve_namespaces",
    "install_signal_handlers",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Port of the ``-kubeconfig`` flag."""
    parser = argparse.ArgumentParser(prog="k8s-sentry", add_help=True)
    parser.add_argument(
        "--kubeconfig", "-kubeconfig", dest="kubeconfig", default="", help="Configuration file"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def resolve_environment(log: Callable[[str], Any] = print) -> str:
    """Port of the SENTRY_ENVIRONMENT / ENVIRONMENT precedence.

    ``ENVIRONMENT`` is honoured but deprecated, and warns only when it is the
    one actually used.
    """
    env = os.environ.get("SENTRY_ENVIRONMENT", "")
    if env == "":
        env = os.environ.get("ENVIRONMENT", "")
        if env != "":
            log(
                "Warning: ENVIRONMENT environment variable has been deprecated. "
                "Please use SENTRY_ENVIRONMENT instead."
            )
    return env


def resolve_namespaces() -> List[str]:
    """Port of the NAMESPACE handling: unset means every namespace."""
    namespace = os.environ.get("NAMESPACE", "")
    if namespace == "":
        return [k8s.NamespaceAll]
    return namespace.split(",")


def install_signal_handlers() -> threading.Event:
    """Port of ``signal.Notify(abortSignal, Interrupt, SIGHUP, SIGTERM)``.

    Returns the event the handler sets. Kept at module level so the handler is
    reachable from a test; installing a handler is not possible off the main
    thread, hence the tolerated failure.
    """
    abort = threading.Event()

    def _handle(signum: int, frame: Optional[FrameType]) -> None:
        abort.set()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            pass
    return abort


def main(argv: Optional[Sequence[str]] = None, log: Callable[[str], Any] = print) -> int:
    args = parse_args(argv)

    defaultEnvironment = resolve_environment(log)
    if os.environ.get("SENTRY_DSN", "") == "":
        log("Warning: SENTRY_DSN environment variable not set. Can not report to Sentry")

    hub = SentryHub(
        dsn=os.environ.get("SENTRY_DSN", ""),
        environment=defaultEnvironment,
    )

    clientset, err = createKubernetesClient(args.kubeconfig)
    if err is not None:
        hub.CaptureException(err)
        log("Error creating kubernetes client: {}".format(err))
        return 1

    app = application(
        clientset=clientset,
        # Go reads SENTRY_ENVIRONMENT again here rather than reusing the
        # resolved value, so a deprecated ENVIRONMENT does not reach the events.
        defaultEnvironment=os.environ.get("SENTRY_ENVIRONMENT", ""),
        namespaces=resolve_namespaces(),
        hub=hub,
    )

    stop, err = app.Run()
    if err is not None:
        hub.CaptureException(err)
        log("Error starting monitors: {}".format(err))
        return 1

    abort = install_signal_handlers()
    abort.wait()
    stop.set()
    log("Exiting")
    hub.Flush(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
