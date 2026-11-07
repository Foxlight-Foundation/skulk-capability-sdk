"""Version-one private wire contracts; public descriptors remain Skulk-owned."""

import hashlib
import json
from ipaddress import ip_address
from typing import Annotated, Literal, NewType, Self, final
from urllib.parse import urlsplit

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

HostId = NewType("HostId", str)
TransportNodeId = NewType("TransportNodeId", str)
CapabilityNodeId = NewType("CapabilityNodeId", str)
BundleId = NewType("BundleId", str)
CapabilityId = NewType("CapabilityId", str)
CallId = NewType("CallId", str)
OperationId = NewType("OperationId", str)
ProviderResourceId = NewType("ProviderResourceId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:@-]+$")
]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$")]
MAX_FRAME_BYTES = 65536
"""Largest single message on the local wire, in either direction."""

MAX_CAPABILITY_DESCRIPTORS = 16
"""Largest declared capability set, including read, stream and job contracts."""

MAX_MANIFEST_BYTES = 262144
"""Largest canonical manifest, independently bounded from its signed envelope.

A signed runtime release carries this manifest within a separate metadata
bound. The publisher measures both before signing so an author encounters an
overflow at build time rather than after capability installation.
"""

EXTENDED_CONTRACTS_SKULK_VERSION = "2.0.3"
"""First reader for expanded contract counts, manifests or metadata envelopes.

The compatible reader must be deployed before publishing expanded metadata.
Small existing manifests keep their bytes, protocol and compatibility window.
"""

MAX_STARTUP_BYTES = MAX_MANIFEST_BYTES + MAX_FRAME_BYTES
"""Largest startup record: the manifest plus one frame of room for the
installation's identity, its settings and its credential path."""


PROTOCOL = 4
"""The manifest and wire protocol this SDK speaks.

Protocol 4 adds isolated, bounded duplex media channels for managed streams.
Protocol 3 adds the host's serve address to the startup record
(``Startup.serve_host``), and its owners carry an installation's settings
across a schema change that still accepts them. Protocol 2 bounds the child's
startup record by the manifest's own limit and answers the hello with the
manifest's digest. Protocol 1, no longer accepted, read the record with one
wire frame and echoed every descriptor in the hello.
"""

ACCEPTED_PROTOCOLS: tuple[int, ...] = (3, 4)
"""Protocols a host or owner built from this SDK accepts.

The current protocol and, once there is one, the previous: a capability built
against the previous SDK keeps installing for one release cycle, and a
refusal names the exact mismatch rather than failing somewhere in a range.
The window never has more than two members, so both are always tested.
"""

QUALIFIED_SKULK_VERSION = "2.0.1"
"""The Skulk release this SDK is qualified against, ``qualified_version`` in
``skulk-baseline.json``. An integration source pin may be newer without promoting
runtime qualification. The owner checks legacy manifests against it because
they carry no core-attested version."""

SKULK_REQUIRES_DEFAULT = (
    f">={QUALIFIED_SKULK_VERSION},<{int(QUALIFIED_SKULK_VERSION.split('.')[0]) + 1}"
)
"""What a manifest admits unless it declares otherwise: the qualified release
and every later release of the same major version. An exact pin made every
Skulk patch release refuse capabilities built for the one before it; the
protocol windows already refuse a host whose wire is incompatible, by name,
and a new major version is where a capability must be rebuilt."""


def accepted_protocol(value: int) -> int:
    """Refuse a protocol outside the window, naming what this SDK accepts."""
    if value not in ACCEPTED_PROTOCOLS:
        accepted = " or ".join(str(item) for item in ACCEPTED_PROTOCOLS)
        raise ValueError(
            f"protocol {value} is not accepted; this SDK accepts {accepted}"
        )
    return value


Protocol = Annotated[
    int,
    AfterValidator(accepted_protocol),
    # The exported JSON Schema states the window too, so an independent
    # validator refuses the same values this model refuses.
    Field(json_schema_extra={"enum": list(ACCEPTED_PROTOCOLS)}),
]
"""A protocol number inside the accepted window."""


class Contract(BaseModel):
    """Strict immutable envelope; serialize at ownership boundaries."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


METADATA_ADMISSION_ANNOTATION = "skulk.admission"
"""Descriptor annotation selecting the bounded cached-metadata admission lane."""
METADATA_ADMISSION = "metadata"
"""Opt-in for empty-input, cached, side-effect-free unary reads during a stream."""


@final
class Descriptor(Contract):
    """Public capability descriptor with four independently typed I/O modes."""

    id: Identifier = Field(description="Public capability identifier.")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", description="Semantic version.")
    title: str = Field(min_length=1, max_length=128, description="Operator title.")
    description: str = Field(
        min_length=1, max_length=2048, description="Capability behavior."
    )
    input_schema: dict[str, JsonValue] = Field(description="Public input JSON Schema.")
    output_schema: dict[str, JsonValue] | None = Field(
        default=None, description="Public final-result JSON Schema."
    )
    io_mode: Literal[
        "unary", "server_streaming", "client_streaming", "bidirectional"
    ] = "unary"
    input_chunk_schema: dict[str, JsonValue] | None = None
    output_chunk_schema: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def chunk_schemas_match_mode(self) -> Self:
        """Require precisely the chunk schemas consumed by the selected I/O mode."""
        for schema, required in (
            (
                self.input_chunk_schema,
                self.io_mode in ("client_streaming", "bidirectional"),
            ),
            (
                self.output_chunk_schema,
                self.io_mode in ("server_streaming", "bidirectional"),
            ),
        ):
            if (schema is not None) != required:
                raise ValueError("chunk schemas do not match the I/O mode")
        admission = (self.annotations or {}).get(METADATA_ADMISSION_ANNOTATION)
        if admission is not None and admission != METADATA_ADMISSION:
            raise ValueError("unsupported managed admission lane")
        if admission == METADATA_ADMISSION and (
            self.io_mode != "unary"
            or self.input_schema != {"type": "object", "additionalProperties": False}
            # Python equates zero with False; JSON Schema requires a boolean.
            or self.input_schema.get("additionalProperties") is not False
        ):
            raise ValueError(
                "metadata admission requires an empty-input unary descriptor"
            )
        return self

    annotations: dict[str, str] | None = None

    @property
    def metadata_admission(self) -> bool:
        """Whether this descriptor promises a cached read on the metadata lane."""
        return (self.annotations or {}).get(
            METADATA_ADMISSION_ANNOTATION
        ) == METADATA_ADMISSION

    @property
    def qualified_id(self) -> str:
        """Return the exact public negotiation key."""
        return f"{self.id}@{self.version}"

    @property
    def revision(self) -> str:
        """Match the pinned public canonical descriptor revision algorithm."""
        return hashlib.sha256(canonical(self)).hexdigest()[:16]


@final
class Bounds(Contract):
    """Hard local admission, liveness, restart and output limits."""

    max_children: int = Field(default=4, ge=1, le=16)
    concurrency: Literal[1] = Field(
        default=1,
        description=(
            "One workload slot per child; declared metadata reads have "
            "one separate slot."
        ),
    )
    startup_seconds: float = Field(default=5.0, gt=0, le=30)
    health_seconds: float = Field(default=1.0, gt=0, le=10)
    call_seconds: float = Field(default=5.0, gt=0, le=30)
    stream_seconds: float = Field(
        default=300.0,
        gt=0,
        le=300,
        exclude_if=lambda value: value == 300.0,
        description="Single deadline ceiling for a streaming call, including cleanup.",
    )
    shutdown_seconds: float = Field(default=1.0, gt=0, le=5)
    restart_budget: int = Field(default=3, ge=0, le=10)
    backoff_seconds: float = Field(default=0.1, gt=0, le=10)
    log_bytes: Literal[0] = Field(
        default=0, description="No child log payload retention."
    )


SurfaceId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
SURFACE_URL_MAX_LENGTH = 512
OPERATION_ANNOTATION = "skulk.operation"
"""Descriptor annotation key marking a durable operation contract."""


def _public_link(url: str) -> str:
    """Accept only explicit http(s) links with a path and nothing else.

    A link is opened by an operator's browser and is replicated in owner
    descriptions, so it must never carry userinfo, a query, a fragment, or a
    non-web scheme: bearer tickets travel in query and fragment parameters
    under any name, and a name screen cannot fail closed. A surface that
    needs an authenticated URL must authenticate in its own page; one that
    needs parameters must put them in the path. Reachability is the
    browser's problem: a loopback link is usable only from the host itself
    and is never rewritten.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("surface links must be explicit http or https URLs")
    if "@" in parts.netloc:
        raise ValueError("surface links must not embed credentials")
    if parts.query or parts.fragment or url.rstrip().endswith(("?", "#")):
        raise ValueError("surface links must not carry a query or fragment")
    return url


@final
class Surface(Contract):
    """One declared operator-facing web surface of a capability node.

    Identity, kind and permissions are immutable manifest facts; readiness and
    the volatile endpoint arrive in correlated health replies. ``proxied``
    surfaces are declared so the manifest is complete, but their endpoints are
    withheld from every projection until the proxy contract is qualified.
    """

    surface_id: SurfaceId
    title: str = Field(min_length=1, max_length=64)
    kind: Literal["proxied", "link"]
    entry: str = Field(default="/", max_length=256)
    websocket: bool = False

    @model_validator(mode="after")
    def kind_specific_fields(self) -> Self:
        """Require proxied-only fields to stay at their defaults for links."""
        if self.kind == "link":
            if self.entry != "/" or self.websocket:
                raise ValueError("link surfaces carry no proxied entry or websocket")
            return self
        if not self.entry.startswith("/") or ".." in self.entry:
            raise ValueError("proxied entry must be an absolute path without ..")
        return self


@final
class SurfaceReport(Contract):
    """Volatile readiness of one declared surface, reported with health.

    A complete snapshot is expected: a declared surface absent from the reply
    is not ready and any earlier endpoint is withdrawn.
    """

    surface_id: SurfaceId
    ready: bool
    port: int | None = Field(default=None, ge=1024, le=65535)
    url: str | None = Field(default=None, max_length=SURFACE_URL_MAX_LENGTH)

    @model_validator(mode="after")
    def link_urls_are_public(self) -> Self:
        """Validate a link endpoint as soon as it is reported."""
        if self.url is not None:
            _public_link(self.url)
        return self


@final
class OperationBounds(Contract):
    """Limits for durable operations a node runs outside the invocation slot."""

    max_queued: int = Field(default=4, ge=1, le=64)
    max_runtime_seconds: int = Field(default=3600, ge=1, le=86400)
    stale_after_seconds: int = Field(default=30, ge=1, le=3600)
    retain: int = Field(default=64, ge=1, le=1024)
    log_bytes: int = Field(default=262144, ge=0, le=16 * 1024 * 1024)
    reservations: int = Field(
        default=65536,
        ge=64,
        le=1_000_000,
        description=(
            "Replay-fence capacity. Reaching it refuses new starts rather than "
            "forgetting old ids, which would make them replayable."
        ),
    )


@final
class StewardExposure(Contract):
    """Trusted private manifest classification; never inferred from tool text.

    ``read`` exposures become direct steward tools; ``lifecycle`` and
    ``operation`` exposures become inert proposals that need an approval. A
    declaration is not evidence of enforcement: the owner still validates the
    manifest and the child still checks authority at dispatch.
    """

    qualified_id: Identifier
    behavior: Literal["read", "lifecycle", "operation"]
    risk: Literal["observation", "billable", "effect"]
    provider_selection: (
        Literal["runpod-simulator", "runpod-witness-experimental"] | None
    ) = Field(
        default="runpod-simulator",
        description=(
            "Governed provider for lifecycle exposures; None only for read and "
            "operation exposures that select no provider."
        ),
    )
    summary: str | None = Field(
        default=None,
        max_length=256,
        exclude_if=lambda value: value is None,
        description="Model-facing tool description; omitted means the descriptor's.",
    )


class CredentialInput(Contract):
    """One plugin-declared write-only credential; values are never manifest data."""

    credential_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$",
        description="Stable node-local credential reference.",
    )
    title: str = Field(
        min_length=1, max_length=128, description="Owner-facing credential label."
    )
    description: str = Field(
        max_length=2048,
        description="What this credential authorizes and where to obtain it.",
    )
    required: bool = Field(
        default=True, description="Whether readiness requires a usable value."
    )


@final
class Manifest(Contract):
    """Owner-installed immutable bundle contract; no secrets or PATH discovery."""

    protocol: Protocol = PROTOCOL
    bundle_id: Identifier
    bundle_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    skulk_requires: str = Field(default=SKULK_REQUIRES_DEFAULT, max_length=128)
    executable: str = Field(
        min_length=1, max_length=4096, description="Absolute approved executable."
    )
    executable_sha256: Digest
    arguments: tuple[str, ...] = Field(max_length=16)
    descriptors: tuple[Descriptor, ...] = Field(
        min_length=1, max_length=MAX_CAPABILITY_DESCRIPTORS
    )
    bounds: Bounds = Field(default_factory=Bounds)
    effect: Literal["read", "governed"] = "read"
    steward: tuple[StewardExposure, ...] = Field(default=(), max_length=8)
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        exclude_if=lambda value: value is None,
        description="Operator-facing bundle title; the bundle id otherwise.",
    )
    surfaces: tuple[Surface, ...] = Field(
        default=(),
        max_length=4,
        exclude_if=lambda value: not value,
        description="Declared web surfaces; readiness rides health replies.",
    )
    operations: OperationBounds | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Durable-operation limits; required by operation descriptors.",
    )
    configuration_schema: dict[str, JsonValue] | None = Field(
        default=None,
        description="Ordinary-settings schema; configured bundles start disabled.",
    )
    credential_inputs: tuple[CredentialInput, ...] = Field(
        default=(),
        max_length=16,
        exclude_if=lambda value: not value,
        description="Write-only inputs for the provisioned node credential store.",
    )

    runtime_dependencies: tuple[
        Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128)],
        ...,
    ] = Field(
        default=(),
        max_length=32,
        exclude_if=lambda value: not value,
        description=(
            "Canonical distribution names this capability adds as runtime roots. "
            "Exact versions and wheel bytes belong to the signed runtime inventory."
        ),
    )

    preflight_profile: Literal["local", "runpod-native"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Signed owner preflight profile enforced at enable and restart.",
    )

    @model_validator(mode="after")
    def unique_contracts(self) -> Self:
        """Reject duplicate contracts and malformed compatibility requirements."""
        if len(set(self.runtime_dependencies)) != len(self.runtime_dependencies):
            raise ValueError("duplicate runtime dependency")
        if "pip" in self.runtime_dependencies:
            raise ValueError("installer tools cannot be runtime dependency roots")
        if self.protocol < 4 and (
            any(d.io_mode != "unary" for d in self.descriptors)
            or self.bounds.stream_seconds != 300.0
        ):
            raise ValueError("streaming requires protocol 4")
        if len({d.qualified_id for d in self.descriptors}) != len(self.descriptors):
            raise ValueError("duplicate capability contract")
        if len({item.credential_id for item in self.credential_inputs}) != len(
            self.credential_inputs
        ):
            raise ValueError("duplicate credential input")
        if self.preflight_profile is not None and self.configuration_schema is None:
            raise ValueError("preflight requires managed configuration")
        size = len(canonical(self))
        if size > MAX_MANIFEST_BYTES:
            raise ValueError(
                f"manifest is {size} bytes; the bound is {MAX_MANIFEST_BYTES}"
            )
        if self.preflight_profile == "runpod-native" and (
            self.effect != "governed"
            or len(self.descriptors) != 1
            or not {
                "witness-rpc",
                "authority-rpc",
                "cleanup-ssh-key",
                "bootstrap-client-key",
            }
            <= {item.credential_id for item in self.credential_inputs if item.required}
        ):
            raise ValueError(
                "native preflight requires exact governed capability and credentials"
            )
        known = {d.qualified_id for d in self.descriptors}
        operations = {
            d.qualified_id
            for d in self.descriptors
            if (d.annotations or {}).get(OPERATION_ANNOTATION) == "1"
        }
        if operations and self.operations is None:
            raise ValueError("operation descriptors require manifest operations bounds")
        if len({item.qualified_id for item in self.steward}) != len(self.steward):
            raise ValueError("duplicate steward exposure")
        for item in self.steward:
            if (
                item.qualified_id not in known
                or (
                    item.behavior == "read"
                    and (self.effect != "read" or item.risk != "observation")
                )
                or (
                    item.behavior == "lifecycle"
                    and (
                        self.effect != "governed"
                        or item.risk != "billable"
                        or item.provider_selection is None
                    )
                )
                or (
                    item.behavior == "operation"
                    and (
                        self.effect != "governed"
                        or item.risk == "observation"
                        or item.qualified_id not in operations
                        # An operation exposure never routes through the
                        # governed provider tools; a provider here would
                        # advertise the wrong proposal workflow.
                        or item.provider_selection is not None
                    )
                )
            ):
                raise ValueError(
                    "steward effect classification does not match manifest"
                )
        if len({item.surface_id for item in self.surfaces}) != len(self.surfaces):
            raise ValueError("duplicate surface")
        SpecifierSet(self.skulk_requires)
        if len(self.descriptors) > 8 or size > 131072:
            require_extended_contracts_reader(self.skulk_requires)
        return self


def require_extended_contracts_reader(skulk_requires: str) -> None:
    """Require an explicit compatible version floor for expanded metadata.

    Args:
        skulk_requires: The release's declared Skulk version range.

    Raises:
        ValueError: The range is invalid or could admit a reader preceding the
            extended contract implementation. Publisher-only discovery and
            signed-envelope measurements use the same check as authoring.
    """
    if not _explicit_reader_floor(
        SpecifierSet(skulk_requires), EXTENDED_CONTRACTS_SKULK_VERSION
    ):
        raise ValueError(
            "expanded capability contracts require an explicit Skulk "
            f"{EXTENDED_CONTRACTS_SKULK_VERSION} or later lower bound"
        )


def _explicit_reader_floor(requirements: SpecifierSet, minimum: str) -> bool:
    # Sampling a few releases cannot prove a lower bound: e.g. an exclusion
    # might skip those samples while still admitting other obsolete readers.
    # Require an actual lower-bound or exact-prefix clause in the conjunction.
    floor = Version(minimum)
    for requirement in requirements:
        if requirement.operator not in (">=", ">", "~=", "==", "==="):
            continue
        version = requirement.version
        if requirement.operator == "==" and version.endswith(".*"):
            version = version[:-2]
        try:
            if Version(version) >= floor:
                return True
        except InvalidVersion:
            continue
    return False


class Message(Contract):
    """Every IPC message pins a protocol revision inside the accepted window."""

    protocol: Protocol = PROTOCOL


@final
class Hello(Message):
    """Authenticated startup identity and exact manifest agreement.

    The child answers with the digest of the manifest it read from its startup
    record rather than echoing the manifest back: the check is the same, and
    the message stays small however much a capability declares.
    """

    kind: Literal["hello"] = "hello"
    host_id: Identifier
    transport_node_id: Identifier
    capability_node_id: Identifier
    bundle_id: Identifier
    bundle_version: str
    token: str = Field(min_length=64, max_length=64, repr=False)
    manifest_sha256: Digest = Field(
        description=(
            "SHA-256 of the canonical manifest the child read from its startup "
            "record (manifest_digest); the owner compares it with its own."
        ),
    )


@final
class Health(Message):
    """Correlated challenge/response; readiness is not inferred from a PID."""

    kind: Literal["health"] = "health"
    nonce: Identifier
    ready: bool
    surfaces: tuple[SurfaceReport, ...] = Field(
        default=(),
        max_length=4,
        exclude_if=lambda value: not value,
        description="Complete readiness snapshot of the declared surfaces.",
    )

    @model_validator(mode="after")
    def unique_surface_reports(self) -> Self:
        """Refuse a reply that reports one surface twice."""
        if len({item.surface_id for item in self.surfaces}) != len(self.surfaces):
            raise ValueError("duplicate surface report")
        return self


@final
class Invoke(Message):
    """One bounded unary invocation; execution time is a remaining local budget."""

    kind: Literal["invoke"] = "invoke"
    call_id: Identifier
    capability_id: Identifier
    version: str
    descriptor_revision: Revision
    remaining_seconds: float = Field(gt=0, le=30)
    payload: dict[str, JsonValue]


@final
class StreamInvoke(Message):
    """Admit one duplex invocation on its separately authenticated media channel."""

    kind: Literal["stream_invoke"] = "stream_invoke"
    protocol: Protocol = Field(default=4, ge=4, le=4)
    call_id: Identifier
    capability_id: Identifier
    version: str
    descriptor_revision: Revision
    remaining_seconds: float = Field(gt=0, le=300)
    payload: dict[str, JsonValue]


@final
class StreamHello(Message):
    """Bind a fresh media connection to the installed child and admitted call."""

    kind: Literal["stream_hello"] = "stream_hello"
    protocol: Protocol = Field(default=4, ge=4, le=4)
    call_id: Identifier
    token: str = Field(min_length=64, max_length=64, repr=False)
    capability_node_id: Identifier
    manifest_sha256: Digest


@final
class Result(Message):
    """Correlated success or bounded typed failure, with one terminal only."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "payload": {"type": "object"},
                        "error": {"type": "null"},
                    }
                },
                {
                    "properties": {
                        "payload": {"type": "null"},
                        "error": {"type": "string"},
                    }
                },
            ]
        },
    )

    kind: Literal["result"] = "result"
    call_id: Identifier
    payload: dict[str, JsonValue] | None
    error: Literal["invalid_payload", "provider_error", "timeout"] | None

    @model_validator(mode="after")
    def one_outcome(self) -> Self:
        """Require exactly one terminal outcome."""
        if (self.payload is None) == (self.error is None):
            raise ValueError("exactly one result outcome is required")
        return self


@final
class Shutdown(Message):
    """Request graceful shutdown before the supervisor escalates."""

    kind: Literal["shutdown"] = "shutdown"


WireMessage = Annotated[
    Hello | Health | Invoke | StreamInvoke | StreamHello | Result | Shutdown,
    Field(discriminator="kind"),
]
WIRE: TypeAdapter[WireMessage] = TypeAdapter(WireMessage)


@final
class ApprovedEffect(Contract):
    """Exact signed effect bounds, optionally tied to a complete reviewed proposal."""

    authority_id: Identifier
    key_id: Identifier
    operator_id: Identifier
    host_id: Identifier
    transport_node_id: Identifier
    capability_node_id: Identifier
    capability_id: Identifier
    capability_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    descriptor_revision: Revision
    plan_digest: Digest
    proposal_digest: Digest | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Complete immutable proposal digest for owner-reviewed approvals; "
            "omitted only by the legacy direct approval path."
        ),
    )
    idempotency_key: Identifier
    campaign_policy_digest: Digest | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Complete reviewed campaign policy; absent for legacy proofs.",
    )
    effect: Literal["acquire", "release"]
    provider_selection: Identifier
    expires_at_unix_seconds: int = Field(gt=0)
    maximum_lifetime_seconds: int = Field(gt=0, le=86400)
    maximum_total_cost_microunits: int = Field(ge=0)
    currency: Literal["USD"] = "USD"
    policy_revision: Identifier


class Denied(Exception):
    """A sanitized authorization, ownership or readiness refusal.

    Part of the authoring surface: a capability raises it to refuse, and the
    host reports it without the reason leaving the node.
    """


class Uncertain(Exception):
    """Observation is required; a provider mutation must not be assumed absent."""


def canonical(value: BaseModel) -> bytes:
    """Encode finite canonical JSON for wire and revision agreement."""
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def manifest_digest(manifest: "Manifest") -> str:
    """SHA-256 of the canonical manifest, what owner and child agree on at hello."""
    return hashlib.sha256(canonical(manifest)).hexdigest()


def serve_address(value: str) -> str:
    """Refuse a serve address that is not one routable unicast IP address."""
    try:
        address = ip_address(value)
    except ValueError:
        raise ValueError("a serve address is one IP address") from None
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("a serve address is a routable unicast address")
    return value


ServeAddress = Annotated[str, AfterValidator(serve_address)]
"""An address a host lets a child serve on besides loopback."""


@final
class Startup(Contract):
    """What a child reads from its inherited startup descriptor.

    Private inherited descriptor contents, never command-line credentials. Part
    of the authoring surface: a capability's child parses exactly this, with
    ``storage.read_startup``. The owner writes it canonically and refuses to
    start a child whose record would exceed ``MAX_STARTUP_BYTES``.
    """

    socket: str
    stream_socket: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Owner-provisioned media socket, unavailable to Fabric callers.",
    )
    token: str = Field(repr=False)
    node_id: str
    host_id: str
    transport_id: str
    manifest: Manifest
    configuration: dict[str, JsonValue] | None = None
    credential_config: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Provisioned backend configuration; private startup IPC only.",
    )
    serve_host: ServeAddress | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Protocol 3: the address the host lets this child serve on besides "
            "loopback, the node's Tailscale address. Absent when the node has "
            "none, and always at protocol 2. See ``serving``."
        ),
    )

    @model_validator(mode="after")
    def serve_host_matches_protocol(self) -> Self:
        """Refuse a serve address in a record a protocol 2 child would reject."""
        if self.stream_socket is not None and self.manifest.protocol < 4:
            raise ValueError("stream_socket requires protocol 4")
        if self.serve_host is not None and self.manifest.protocol < 3:
            raise ValueError("serve_host is a protocol 3 field")
        return self
