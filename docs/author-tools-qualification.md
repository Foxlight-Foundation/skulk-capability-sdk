# Author tools qualification

This Python authoring milestone keeps the eleven qualified SDK runtime modules
unchanged. It adds MIT author tools extracted from the qualified publisher,
a model-free starter and local harness, explicit signed-feed tooling, and
journaled administration through the existing host API. It does not switch the
installed owner to a native SDK runtime or claim complete TypeScript parity.

The local qualification ran strict typing, lint/format, 163 tests, and positive
wheel/source audits. Release models were compared against the qualified owner
models without importing owner implementation into the public packages. Exact
binary runtime recipes cover macOS arm64 and glibc Linux x86_64/aarch64;
platform CI separately verifies the portable author artifacts.

A clean wheel environment containing neither Core nor private owner packages
created and exercised the starter, verified anonymous runtime acquisition,
built and signed immutable releases, and published only verified artifacts
through an HTTPS feed. A compatible public development host staged and activated
the app through its normal lifecycle. Unified capability calls passed after
activation, update, rollback and restoration. The accepted sequence floor stayed
at the newer sequence. Normal uninstall completed, the task-created installation
was purged, and the other installed apps retained their selections and enabled
state. No model, provider account or inference request was used.

The installed test caught a startup argument mismatch in the initial candidate.
The starter and local harness now use the owner's `--config-fd` convention.
A new immutable release corrected the issue; the earlier candidate was never
reported as ready or qualified. This is why local IPC success and installed
acceptance are separate gates.

The public author workflow currently exercises unary calls. Complete durable
work, four-mode streaming, signed Node installation, cross-node interruption,
registered catalog submission and full Python/TypeScript author journeys remain
separate completion gates. Compiler-free author tools do not remove Core's own
build prerequisites. The private owner binary retains its separate license.
