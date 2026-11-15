"""Public native release metadata and compiled author admission.

These records publish exact artifacts; they do not implement a host, grant
execution rights, or convert native declarations to legacy Python contracts.
The legacy release/runtime readers keep their existing protocol windows.
"""

import hashlib
import importlib
import importlib.metadata
import json
from importlib.resources import files
from typing import Literal, Protocol, Self, cast, final

from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from skulk_capability_sdk.contracts import Contract, Digest, Identifier, canonical

from .release_runtime import ROOT_DEPENDENCIES
from .releases import RELEASE_METADATA_MAX_BYTES
from .runtime_release import RuntimePlatform, RuntimeWheel, canonical_platform

_OBJECT = TypeAdapter(dict[str, JsonValue])
_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
_TEXT = TypeAdapter(str)
_TEXTS = TypeAdapter(tuple[str, ...])
_SCHEMA: TypeAdapter[dict[str, JsonValue] | bool | None] = TypeAdapter(
    dict[str, JsonValue] | bool | None
)
_COHORTS = {
    "0.7.0rc3": "da446b54ddd8996ea21cbd61f1155bc1e840ddfff2e36f435ffc973806c865ae",
    "0.7.0rc4": "3702b717039632845f68557c87d10551480501505230134d973b04644b14e2d2",
}
_PROFILE_NAMES = {
    "portable": "skulk.portable/2",
    "managed": "skulk.managed/2",
    "handshake": "skulk.handshake/2",
    "transport": "skulk.transport/2",
    "composition": "skulk.composition/3",
    "calls": "skulk.calls/2",
}


class NativeRecord(Protocol):
    """Immutable public compiled author record used by optional author hooks."""

    @property
    def digest(self) -> str:
        """Return the actual full native identity."""
        ...

    def canonical(self) -> bytes:
        """Export the compiled reader's canonical declaration."""
        ...


class _Factory(Protocol):
    def from_json(self, name: str, source: str) -> NativeRecord: ...


class _Kernel(Protocol):
    ManagedV2Contract: _Factory

    def portable_profile_digest(self) -> str: ...
    def managed_profile_digest(self) -> str: ...
    def handshake_profile_digest(self) -> str: ...
    def transport_profile_digest(self) -> str: ...
    def composition_profile_digest(self) -> str: ...
    def calls_profile_digest(self) -> str: ...
    def portable_v2_profile_digest(self) -> str: ...
    def managed_v2_profile_digest(self) -> str: ...
    def handshake_v2_profile_digest(self) -> str: ...
    def composition_v2_profile_digest(self) -> str: ...
    def composition_v3_profile_digest(self) -> str: ...
    def calls_v2_profile_digest(self) -> str: ...
    def transport_v2_profile_digest(self) -> str: ...


def native_cohort(version: str) -> dict[str, JsonValue]:
    """Read the exact public candidate receipt, including its binary hashes.

    The packaged bytes come from the public SDK release assets, independently
    hash-pinned here. Reading a receipt confers no installation authority.
    """
    expected = _COHORTS.get(version)
    if expected is None:
        raise ValueError("native publisher cohort is unavailable")
    raw = (
        files("skulk_capability_tools")
        .joinpath("native_cohorts", version + ".json")
        .read_bytes()
    )
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("native publisher cohort receipt differs")
    return _OBJECT.validate_json(raw)


def _kernel() -> _Kernel:
    version = importlib.metadata.version("skulk-capability-kernel")
    native_cohort(version)
    return cast(
        _Kernel, cast(object, importlib.import_module("_skulk_capability_kernel"))
    )


@final
class NativeManifest(Contract):
    """Opaque compiled managed/2 declaration in its published wire projection."""

    canonical_json: str = Field(
        max_length=262144,
        description="Managed/2 JSON canonicalized by the actual public native ABI.",
    )

    @field_validator("canonical_json")
    @classmethod
    def compiled_declaration(cls, value: str) -> str:
        """Use the actual native reader; never a legacy Python author model."""
        return (
            _kernel()
            .ManagedV2Contract.from_json("Manifest", value)
            .canonical()
            .decode()
        )

    @property
    def record(self) -> NativeRecord:
        """Return the public immutable record for optional capability author policy."""
        return _kernel().ManagedV2Contract.from_json("Manifest", self.canonical_json)

    @property
    def digest(self) -> str:
        """Expose the full native identity independently of legacy revisions."""
        return self.record.digest

    def _text(self, name: str) -> str:
        return _TEXT.validate_python(
            _OBJECT.validate_json(self.canonical_json)[name], strict=True
        )

    @property
    def bundle_id(self) -> str:
        """Project the admitted application identity for publication metadata."""
        return self._text("bundle_id")

    @property
    def bundle_version(self) -> str:
        """Project the admitted application version."""
        return self._text("bundle_version")

    @property
    def executable(self) -> str:
        """Project the signed artifact name."""
        return self._text("executable")

    @property
    def executable_sha256(self) -> str:
        """Project the signed executable hash."""
        return self._text("executable_sha256")

    @property
    def skulk_requires(self) -> str:
        """Project the declared Core compatibility range, without granting admission."""
        return self._text("skulk_requires")

    @property
    def runtime_dependencies(self) -> tuple[str, ...]:
        """Project admitted dependency names for separate wheel-closure verification."""
        value = _OBJECT.validate_json(self.canonical_json)["runtime_dependencies"]
        return _TEXTS.validate_json(json.dumps(value))

    @property
    def configuration_schema(self) -> dict[str, JsonValue] | bool | None:
        """Project the native schema for separate host metadata-size admission."""
        value = _OBJECT.validate_json(self.canonical_json)["contracts"]
        return _SCHEMA.validate_python(
            _OBJECT.validate_python(value, strict=True)["configuration_schema"],
            strict=True,
        )

    def with_artifact(self, digest: str) -> "NativeManifest":
        """Re-admit the declaration after binding the actual deterministic archive."""
        value = _OBJECT.validate_json(self.canonical_json)
        value.update(executable="bundle.pyz", executable_sha256=digest)
        return NativeManifest(canonical_json=json.dumps(value))


@final
class NativeProfiles(Contract):
    """Exact public native cohort selection, distinct from author and host authority."""

    version: Literal["0.7.0rc3", "0.7.0rc4"] = Field(
        description="Selected binary version."
    )
    source_commit: str = Field(
        pattern=r"^[a-f0-9]{40}$", description="Public native source identity."
    )
    portable: Digest = Field(description="Active portable/2 fingerprint.")
    managed: Digest = Field(description="Active managed/2 fingerprint.")
    handshake: Digest = Field(description="Active handshake/2 fingerprint.")
    transport: Digest = Field(description="Active transport/2 fingerprint.")
    composition: Digest = Field(description="Active composition/3 fingerprint.")
    calls: Digest = Field(description="Active calls/2 fingerprint.")

    @model_validator(mode="after")
    def exact_cohort(self) -> Self:
        """Refuse crossed source, version or profile claims."""
        receipt = native_cohort(self.version)
        profiles = {
            _TEXT.validate_python(item["profile"], strict=True): item["profile_digest"]
            for item in _OBJECTS.validate_python(receipt["profiles"], strict=True)
        }
        if self.source_commit != receipt["source_commit"] or any(
            getattr(self, field) != profiles[name]
            for field, name in _PROFILE_NAMES.items()
        ):
            raise ValueError("native publisher profiles differ from the public cohort")
        return self

    @classmethod
    def installed(cls) -> "NativeProfiles":
        """Verify all thirteen actual ABI fingerprints before selecting execution."""
        version = importlib.metadata.version("skulk-capability-kernel")
        receipt, kernel = native_cohort(version), _kernel()
        observed = {
            "skulk.portable/1": kernel.portable_profile_digest(),
            "skulk.managed/1": kernel.managed_profile_digest(),
            "skulk.handshake/1": kernel.handshake_profile_digest(),
            "skulk.transport/1": kernel.transport_profile_digest(),
            "skulk.composition/1": kernel.composition_profile_digest(),
            "skulk.calls/1": kernel.calls_profile_digest(),
            "skulk.portable/2": kernel.portable_v2_profile_digest(),
            "skulk.managed/2": kernel.managed_v2_profile_digest(),
            "skulk.handshake/2": kernel.handshake_v2_profile_digest(),
            "skulk.composition/2": kernel.composition_v2_profile_digest(),
            "skulk.composition/3": kernel.composition_v3_profile_digest(),
            "skulk.calls/2": kernel.calls_v2_profile_digest(),
            "skulk.transport/2": kernel.transport_v2_profile_digest(),
        }
        expected = {
            _TEXT.validate_python(item["profile"], strict=True): item["profile_digest"]
            for item in _OBJECTS.validate_python(receipt["profiles"], strict=True)
        }
        if observed != expected:
            raise ValueError(
                "installed native publisher ABI differs from its public cohort"
            )
        return cls.model_validate(
            {
                "version": version,
                "source_commit": receipt["source_commit"],
                **{field: observed[name] for field, name in _PROFILE_NAMES.items()},
            },
            strict=True,
        )


@final
class NativeRelease(Contract):
    """Release-three metadata for a compiled declaration, without legacy projection."""

    protocol: Literal[3] = Field(default=3, description="Native release wire version.")
    publisher: Identifier = Field(description="Externally trusted publisher identity.")
    sequence: int = Field(ge=1, description="Immutable publisher sequence.")
    created_at: int = Field(gt=0, description="Creation Unix seconds.")
    expires_at: int = Field(gt=0, description="Expiry Unix seconds.")
    artifact_name: Literal["bundle.pyz"] = Field(
        default="bundle.pyz", description="Archive basename."
    )
    artifact_size: int = Field(ge=1, le=67108864, description="Exact archive size.")
    manifest: NativeManifest = Field(description="Actual compiled author declaration.")
    platforms: tuple[Literal["darwin", "linux"], ...] = Field(
        min_length=1, max_length=2, description="Declared host systems."
    )
    python_requires: Literal["==3.13.*"] = Field(
        default="==3.13.*", description="Required interpreter."
    )
    skulk_build_sha256: Digest = Field(description="Qualified Core source provenance.")
    dependency_lock_sha256: Digest = Field(
        description="Complete wheel inventory digest."
    )
    state_schema: Identifier = Field(
        description="Capability-owned persistent state schema."
    )
    compatible_state_schemas: tuple[Identifier, ...] = Field(
        max_length=8, description="Explicit compatible schemas."
    )
    permissions: tuple[str, ...] = Field(
        min_length=1,
        max_length=16,
        description="Operator-reviewed permission summaries.",
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def exact_protocol(cls, value: object) -> object:
        """Refuse boolean or floating-point aliases for this explicit version."""
        if type(value) is not int or value != 3:
            raise ValueError("native release protocol must be integer3")
        return value

    @model_validator(mode="after")
    def portable(self) -> Self:
        """Bound artifact, expiry and permission metadata before publication."""
        if (
            self.manifest.executable != self.artifact_name
            or not self.created_at < self.expires_at
        ):
            raise ValueError("native release artifact or expiry differs")
        if any(not 0 < len(value) <= 512 for value in self.permissions):
            raise ValueError("native release permission summary exceeds bound")
        return self

    @property
    def digest(self) -> str:
        """Bind exact metadata with the native release domain."""
        return hashlib.sha256(b"skulk.release/3\x00" + canonical(self)).hexdigest()


@final
class NativeRuntimeRelease(Contract):
    """Runtime-five selection of exact wheels and the public inference cohort."""

    protocol: Literal[5] = Field(default=5, description="Native runtime wire version.")
    implementation: Literal["cpython"] = Field(
        default="cpython", description="Interpreter implementation."
    )
    platform: RuntimePlatform = Field(description="Exact qualified artifact family.")
    release: NativeRelease = Field(description="Underlying native application release.")
    kernel: NativeProfiles = Field(description="Explicit immutable native cohort.")
    control_profile: Literal["skulk.calls/2"] = Field(
        default="skulk.calls/2", description="Selected native execution protocol."
    )
    wheels: tuple[RuntimeWheel, ...] = Field(
        min_length=1, max_length=64, description="Complete exact wheel closure."
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def exact_protocol(cls, value: object) -> object:
        """Refuse numeric-kind coercion before native envelope selection."""
        if type(value) is not int or value != 5:
            raise ValueError("native runtime protocol must be integer5")
        return value

    @property
    def inventory(self) -> dict[str, str]:
        """Return the exact distribution versions the wheel names declare."""
        return dict(sorted(wheel.distribution for wheel in self.wheels))

    @property
    def runtime_dependencies(self) -> tuple[str, ...]:
        """Expose dependency roots to the shared generic wheel verifier."""
        return self.release.manifest.runtime_dependencies

    @property
    def inventory_digest(self) -> str:
        """Bind the complete inventory independently of native record identities."""
        return hashlib.sha256(
            json.dumps(self.inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def complete_inventory(self) -> Self:
        """Refuse ambiguous, incomplete, oversized or crossed native wheel sets."""
        names = [wheel.distribution[0] for wheel in self.wheels]
        if len(set(names)) != len(names) or "pip" in names:
            raise ValueError("native runtime distribution inventory is ambiguous")
        if (
            sum(wheel.size for wheel in self.wheels) > 512 * 1024 * 1024
            or len(canonical(self)) > RELEASE_METADATA_MAX_BYTES
        ):
            raise ValueError("native runtime exceeds metadata or artifact bounds")
        if not (
            {str(canonicalize_name(name)) for name in ROOT_DEPENDENCIES}
            | set(self.runtime_dependencies)
        ) <= set(names):
            raise ValueError("native runtime omits required dependency roots")
        if self.inventory_digest != self.release.dependency_lock_sha256:
            raise ValueError("native runtime inventory differs from the signed release")
        if Version(self.inventory.get("skulk-capability-sdk", "0")) < Version(
            "0.5.10rc1"
        ):
            raise ValueError(
                "native application requires the version-qualified public SDK"
            )
        family = canonical_platform(self.platform)
        system = "darwin" if family.startswith("macos-") else family.split("-", 1)[0]
        if system not in self.release.platforms:
            raise ValueError("native runtime and release platforms differ")
        cohort = native_cohort(self.kernel.version)
        platforms = _OBJECTS.validate_python(cohort["platforms"], strict=True)
        matches = [
            _OBJECT.validate_python(item["wheel"], strict=True)
            for item in platforms
            if item["platform"] == family
        ]
        if len(matches) != 1:
            raise ValueError("native runtime has no published binary for this family")
        expected = matches[0]
        binary = [
            wheel
            for wheel in self.wheels
            if wheel.distribution[0] == "skulk-capability-kernel"
        ]
        if len(binary) != 1 or {
            "name": binary[0].filename,
            "sha256": binary[0].sha256,
            "size": binary[0].size,
        } != {key: expected[key] for key in ("name", "sha256", "size")}:
            raise ValueError(
                "native runtime binary differs from its exact public asset"
            )
        return self

    @property
    def digest(self) -> str:
        """Bind native runtime bytes using the published, separate signing domain."""
        return hashlib.sha256(native_runtime_message(self)).hexdigest()


def native_runtime_message(runtime: NativeRuntimeRelease) -> bytes:
    """Return the exact runtime-five signature input without retyping legacy bytes."""
    return b"skulk.runtime/5\x00" + canonical(runtime)


@final
class SignedNativeRuntimeRelease(Contract):
    """Native runtime signature with publisher trust supplied independently."""

    runtime: NativeRuntimeRelease = Field(description="Exact selected native runtime.")
    signature: str = Field(
        pattern=r"^[a-f0-9]{128}$", description="Native-domain Ed25519 signature."
    )
