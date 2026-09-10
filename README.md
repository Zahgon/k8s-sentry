# k8ssentry

A Python port of [`github.com/wichert/k8s-sentry`](https://github.com/wichert/k8s-sentry)
(`8109a1e`) — watches Kubernetes pods and events and reports failures to Sentry.

The port targets **behavioural equivalence with the Go program**, not idiomatic
redesign. Names, signatures and the emitted Sentry payload follow the Go
original, including its quirks.

## Install

```sh
pip install -e ".[dev]"           # core + test tooling, zero runtime deps
pip install -e ".[cluster,dev]"   # adds the Kubernetes and Sentry clients
```

The transformation — Kubernetes object in, Sentry payload out — has **zero
runtime dependencies**. The cluster and Sentry clients are optional extras used
only by the I/O adapter. Python 3.9+.

## Use

```sh
SENTRY_DSN=https://... NAMESPACE=default k8s-sentry
```

| Variable | Meaning |
| --- | --- |
| `SENTRY_DSN` | Sentry endpoint; a warning is logged when unset |
| `SENTRY_ENVIRONMENT` | Environment tag; falls back to the object's namespace |
| `ENVIRONMENT` | Deprecated alias, warns when used |
| `NAMESPACE` | Comma-separated list; unset means every namespace |
| `--kubeconfig` | Config file; defaults to `~/.kube/config` outside a cluster |

Using it as a library:

```python
from k8ssentry import application, k8s

app = application(defaultEnvironment="prod", hub=my_hub)
app.handleEventAdd(some_event)     # builds and captures the Sentry payload
```

## Equivalence

`verification/` holds the proof, not just tests.

* `go-probe.go.txt` runs **inside the real Go package** (it needs `skipEvent`,
  `fingerprintFromMeta`, the handlers and the event construction, none of which
  are exported). `gen-go-truth.sh` copies it into a throwaway copy of the Go
  checkout, records `go-truth.json`, deletes it, and fails if `git status` in the
  checkout changes.
* `truth_inputs.py` generates the shared corpus; both sides replay the identical
  file so they cannot drift.
* `tests/test_differential.py` requires **zero** divergences.

Covered: `skipEvent`, `getSentryLevel`, `inCluster`, `fingerprintFromMeta`, both
`EventHandler` implementations, and the **complete Sentry payload** built from a
`v1.Event` and from a `v1.Pod`, byte-for-byte.

```sh
make check      # compile + lint + typecheck + test
make truth      # re-record from the real Go program (needs Go on PATH)
```

## Behaviours deliberately preserved

These look like bugs. They are what the Go program does, so the port does them
too — each is pinned by a test.

| Behaviour | Why |
| --- | --- |
| `Tags()` can be `None`, not `{}` | `GetLabels()` hands back Go's **nil map**, which marshals to `null`. Ranging over it is legal in Go and yields nothing. |
| `timestamp` is the **last** key in the payload | sentry-go's custom `MarshalJSON` shadows it in an outer wrapper. |
| `sdk` and `user` always appear | `omitempty` never omits a struct, so a bare event is `{"sdk":{},"user":{}}`. |
| Only exact `Warning` / `Error` map to a level | `getSentryLevel` compares literally, so `warning` (lower case) prints a notice and falls back to `info`. |
| Only the **first** failed container is reported | The scan `break`s, and init containers are scanned before regular ones. |
| A termination older than 5ms is ignored | `isNewTermination` treats anything older as a stale record from an unrelated pod update. |
| The **first** termination of a container is never reported | `Get` runs before `Add`, so the cache is empty on first sight and Go's `cachedTime.(metav1.Time)` assertion fails, returning false. Only a *later* termination with a newer `FinishedAt` is reported. Verified against Go: first `false`, same-time repeat `false`, newer `true`. |
| The cache is written **before** the age check | So a stale record still refreshes the entry. |

## Known differences

* **The I/O edge is not byte-exact.** Informer wiring, kubeconfig discovery and
  Sentry delivery cannot have a Go oracle recorded without a live cluster, so
  `transport.py` and `main.py` are a documented adapter rather than a proven
  port. `verification/SCOPE.md` states this explicitly.
* **Pod-involved events are not exercisable end to end.** `NewPodEventHandler`
  fetches the Pod through the API, and the Go code types the field as the
  concrete `*kubernetes.Clientset` rather than an interface, so it cannot be
  faked on either side. `PodEventHandler.Fingerprint()`/`Tags()` are proven
  directly instead, and the registry fall-through is unit-tested.
* **SDK enrichment is out of scope.** `sentry.CaptureEvent` decorates events with
  the host's architecture, CPU count, hostname and the entire Go module list.
  None of that is computed by this program, and it is excluded from the oracle.

## Licence

Apache-2.0, as upstream — see `LICENSE`. Copyright 2019 Wichert Akkerman.
