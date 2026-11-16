"""Explicit publication readers; native records retain their own signature domain."""

from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.contracts import canonical

from .catalog import signed_record as legacy_record
from .native_publishing import (
    NativeRelease,
    NativeRuntimeRelease,
    SignedNativeRuntimeRelease,
    native_runtime_message,
)
from .releases import Release, SignedRelease
from .runtime_release import RuntimeRelease, SignedRuntimeRelease

type PublicationRecord = (
    SignedRelease | SignedRuntimeRelease | SignedNativeRuntimeRelease
)
type PublicationRelease = Release | NativeRelease
type PublicationRuntime = RuntimeRelease | NativeRuntimeRelease
_OBJECT = TypeAdapter(dict[str, JsonValue])


def read_publication_record(raw: bytes) -> PublicationRecord:
    """Select runtime-five explicitly without widening the legacy readers."""
    value = _OBJECT.validate_json(raw)
    runtime = value.get("runtime")
    if isinstance(runtime, dict) and type(runtime.get("protocol")) is int:
        if runtime["protocol"] == 5:
            record = SignedNativeRuntimeRelease.model_validate_json(raw)
            # Native signatures cover the original claims, including the
            # manifest's canonical byte string. Default insertion or
            # normalization must never make altered served claims verify.
            if _OBJECT.validate_python(runtime, strict=True) != _OBJECT.validate_json(
                record.runtime.model_dump_json()
            ):
                raise ValueError("native publication claims differ from exact input")
            return record
    return legacy_record(raw)


def publication_release(record: PublicationRecord) -> PublicationRelease:
    """Return the exact admitted application release without identity conversion."""
    if isinstance(record, (SignedRuntimeRelease, SignedNativeRuntimeRelease)):
        return record.runtime.release
    return record.release


def publication_runtime(record: PublicationRecord) -> PublicationRuntime | None:
    """Return an explicit runtime closure, or None for a legacy plain bundle."""
    if isinstance(record, (SignedRuntimeRelease, SignedNativeRuntimeRelease)):
        return record.runtime
    return None


def publication_message(record: PublicationRecord) -> bytes:
    """Reconstruct only the version-qualified signature input the record declares."""
    if isinstance(record, SignedNativeRuntimeRelease):
        return native_runtime_message(record.runtime)
    if isinstance(record, SignedRuntimeRelease):
        return canonical(record.runtime)
    return canonical(record.release)


def publication_digest(record: PublicationRecord) -> str:
    """Return the full original native or legacy signed-claims identity."""
    runtime = publication_runtime(record)
    return runtime.digest if runtime is not None else publication_release(record).digest
