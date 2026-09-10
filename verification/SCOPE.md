# Dependency scope decision

Recorded **before** porting, per the migration workflow. Every import the Go
program reaches for is classified here, with the reason.

Source: `wichert/k8s-sentry` @ `8109a1e`, Go 1.13, Apache-2.0.

This repo differs in kind from a self-contained library. It is `package main` —
a daemon — and its dependencies are the Kubernetes client (`client-go` alone is
on the order of 500k lines) and the Sentry SDK. Porting those is not a coherent
goal. What *is* coherent, and what this migration proves, is the transformation
underneath: **given a Kubernetes `Pod` or `Event`, produce a Sentry payload.**

---

## Decision summary

| Dependency | Kind | Decision | Why |
|---|---|---|---|
| `k8s.io/api`, `k8s.io/apimachinery` | third-party | **Model the fields actually read** | Hundreds of thousands of generated lines. Only the fields this code touches are modelled (`_k8s.py`), which keeps the transformation dependency-free and diffable. |
| `k8s.io/client-go` | third-party | **Adapter boundary** | The REST/informer machinery is I/O, not logic. It sits behind `transport.py`; nothing in the proven core imports it. |
| `github.com/getsentry/sentry-go` | third-party | **Payload reproduced byte-for-byte** | The payload *is* the product. `_sentry.py` reproduces `Event` and its custom `MarshalJSON`. The Python `sentry-sdk` is **not** used for it — its event model and serialisation differ. |
| `github.com/hashicorp/golang-lru` | third-party | **Ported** | Small, and its recency semantics decide whether a termination is reported twice. `_lru.py`. |
| `encoding/json` | stdlib | **Ported** | HTML escaping on, map keys sorted by UTF-8 bytes, `omitempty`, structs never omitted. `_gojson.py`. |
| `time` | stdlib | **Shim formatting** | RFC3339 with trailing zeros trimmed, and nanosecond precision `datetime` cannot hold. |
| `os`, `strconv`, `fmt`, `log` | stdlib | **Direct equivalent** | Env lookup, integer formatting, message building. |

---

## What the differential proves, and what it cannot

`verification/go-truth.json` records **113 cases over 8 kinds**, replayed with
zero divergences:

| kind | proves |
|---|---|
| `eventtype` | `skipEvent` and `getSentryLevel` over 15 type strings |
| `incluster` | `inCluster` over 7 env combinations |
| `fingerprintmeta` | `fingerprintFromMeta`, including the controller-owner rules |
| `defaulthandler` | `DefaultEventHandler.Fingerprint()`/`Tags()` |
| `podhandler` | `PodEventHandler.Fingerprint()`/`Tags()` |
| `eventadd` | the **whole Sentry payload** built from a `v1.Event` |
| `podupdate` | the **whole Sentry payload** built from a `v1.Pod` |
| `sentryjson` | `Event.MarshalJSON` directly, independent of the k8s plumbing |

### The oracle records what the code builds, not what the SDK adds

The first recording was contaminated. `sentry.CaptureEvent` runs the SDK's
integrations, which injected this machine's CPU count and architecture, the
hostname, `platform: "go"`, the SDK version, and the **entire Go module list**
with pinned versions. None of that is computed by `k8s-sentry`, and most is
meaningless in Python.

The probe therefore disables integrations, clears `Platform`/`Sdk`, and pins
`ClientOptions.ServerName` to `probe-host`. Pinning rather than blanking is
deliberate: it keeps "the SDK substituted a hostname" distinguishable from "the
code set `pod.Spec.NodeName`", which is a real branch in `handlePodUpdate`.

### Non-determinism that had to be removed

Found by recording three times and diffing, not by reasoning:

* An `Event` with an empty `CreationTimestamp` leaves `Event.Timestamp` zero, and
  the SDK then stamps `time.Now()` at capture. Only that case is normalised, so
  the recorded timestamp stays verifiable everywhere else.
* The pod path's timestamp is likewise SDK-stamped and is normalised.

### Coverage limit: Pod-involved events

`NewPodEventHandler` fetches the Pod through the API, and the Go code types the
field as the **concrete** `*kubernetes.Clientset` rather than an interface — so
it cannot be substituted with `client-go`'s own fake. An event whose involved
object is a Pod is therefore not exercisable end-to-end on either side without a
live cluster.

This is a boundary, not an oversight, and it is handled explicitly:

* the corpus routes `eventadd` cases through **non-Pod** involved objects, where
  the registry falls through to `DefaultEventHandler`;
* `PodEventHandler.Fingerprint()`/`Tags()` are proven directly by the
  `podhandler` kind;
* the registry dispatch itself is covered by unit tests, including the
  fall-through when the factory returns nil.

## Out of scope

`main.go`'s flag parsing, kubeconfig discovery, informer wiring and signal
handling are I/O. They are ported for completeness in `transport.py`/`main.py`
but are not part of the byte-exact claim: there is no way to record a Go oracle
for them without a cluster, and asserting equivalence without evidence would be
worse than stating the limit.
