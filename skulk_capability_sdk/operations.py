"""Durable operations: bounded jobs a node runs outside the invocation slot.

The unary contract allows one call at a time with a thirty-second budget, so a
render that takes minutes cannot run inside a call. An operation descriptor
turns ``start`` into a bounded acceptance: the call reserves an operation id
in a durable journal and returns at once; a single worker submits the job to
its backend, polls it, and records the outcome; ``status``, ``list`` and
``log`` are reads that never trigger work.

Contract conditions carried from the shared review:

- Idempotency is scoped to the installed node, the exact descriptor identity
  and the canonical input. The same operation id with the same digest returns
  the existing status; a different digest is refused. Reservations are kept
  independently of the display retention, so a pruned row cannot make its old
  id new.
- The backend job identity is persisted and an uncertain submission (the
  backend may or may not have accepted) is distinguished from an accepted one;
  a restart marks queued and running rows ``interrupted`` and nothing replays.
- Cancellation goes through the backend adapter; the runner never signals a
  process group it does not exclusively own.
- Read verbs and effect verbs are dispatched under separate authority.
- Logs are bounded, offset-addressed and owned by the child; redaction is the
  adapter's job before a line reaches the log.
"""

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol, Self, cast, final

from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate as validate_schema
from pydantic import Field, JsonValue, model_validator

from .contracts import (
    OPERATION_ANNOTATION,
    Contract,
    Descriptor,
    Digest,
    Identifier,
    OperationBounds,
)
from .database import Database

OperationState = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
]
"""Lifecycle of one operation; ``interrupted`` means observation is needed."""

Submission = Literal["pending", "uncertain", "accepted"]
"""Whether the backend is known to hold the job."""

ReadVerb = Literal["status", "list", "log"]
EffectVerb = Literal["start", "cancel", "plan"]
Verb = Literal["start", "status", "cancel", "list", "log", "plan"]
Authority = Literal["read", "effect"]

MAX_RESULT_BYTES = 8192
MAX_INPUT_ECHO_BYTES = 24576
"""Largest canonical input a ``status`` reply echoes. With the result bound
and the envelope it stays inside one wire frame; a larger input is kept in
the journal but not echoed."""
MAX_LOG_CHUNK_BYTES = 8192
"""Raw bytes per log page. JSON escaping expands a byte at most six-fold
(``\\u0000``), so the encoded page stays under the 64 KiB frame by proof."""
MAX_REFERENCE_LENGTH = 1024
"""Bound on a backend job reference; it is stored exactly or refused."""
MAX_OFFSET = 2**53
"""Bound on a caller-supplied page or log offset; SQLite and file seeks
represent it exactly, and no journal or log ever reaches it."""
PAGE_SIZE = 16
OVERDUE_OBSERVE_FAILURES = 3
OVERDUE_ERROR = "runtime bound exceeded and the backend stopped answering"
OVERDUE_RETRY_SECONDS = 60.0
MIN_WAIT_SECONDS = 0.05
"""Smallest window an adapter call gets right before the bound, so the loop
reaches the deadline check rather than waiting on a zero timeout."""
"""How long an idle worker waits before observing overdue outstanding rows again."""
"""Consecutive failed observations that end an operation once its runtime
bound has passed; before the bound, observation failures are transient."""
"""Listing page size, provable against the 64 KiB frame: a listed status omits
its result and backend reference, its identifier and digest cannot escape,
and stage plus error escape to at most 1,920 bytes, so a status encodes under
2.5 KiB and sixteen stay under 40 KiB with the envelope."""


@final
class OperationStatus(Contract):
    """Observation envelope of one operation; opaque references, safe errors."""

    operation_id: Identifier
    state: OperationState
    submission: Submission
    progress: float | None = Field(default=None, ge=0, le=1)
    stage: str | None = Field(default=None, max_length=64)
    queue_position: int | None = Field(default=None, ge=0)
    submitted_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    stale: bool
    input_digest: Digest
    backend_reference: str | None = Field(default=None, max_length=MAX_REFERENCE_LENGTH)
    input: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "The recorded start input, so a caller can run the operation again "
            "or vary it. Carried by ``status`` when it fits beside the result "
            "(``MAX_INPUT_ECHO_BYTES``); listings omit it."
        ),
    )
    result: dict[str, JsonValue] | None = None
    error: str | None = Field(default=None, max_length=256)


@final
class OperationCall(Contract):
    """One verb of the operation contract; the dispatcher checks authority."""

    op: Verb
    operation_id: Identifier | None = None
    input: dict[str, JsonValue] | None = None
    offset: int | None = Field(default=None, ge=0, le=MAX_OFFSET)

    @model_validator(mode="after")
    def verb_shape(self) -> Self:
        """Require exactly the fields each verb needs."""
        if self.op in ("start", "plan") and self.input is None:
            raise ValueError(f"{self.op} requires input")
        if self.op in ("status", "cancel", "log") and self.operation_id is None:
            raise ValueError(f"{self.op} requires operation_id")
        if self.op == "list" and (self.operation_id or self.input):
            raise ValueError("list takes only a page offset")
        if self.op not in ("log", "list") and self.offset is not None:
            raise ValueError("offset applies to log and list only")
        return self


@final
class OperationPlan(Contract):
    """Immutable estimate a caller reviews before ``start``."""

    plan_digest: Digest
    summary: str = Field(min_length=1, max_length=512)
    estimated_seconds: int | None = Field(default=None, ge=0)
    estimated_cost_microunits: int | None = Field(default=None, ge=0)
    expires_at: int = Field(ge=0)
    input: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "The input to start with: the caller's, completed with what the "
            "plan chose (a seed, for one) and carrying ``plan_digest``, so the "
            "reviewed plan is the one that starts. Absent when the operation "
            "chooses nothing beyond the caller's input."
        ),
    )


@final
class OperationPage(Contract):
    """One page of retained operations, newest first, compacted.

    Results and backend references can each reach their bounds, so a listing
    carries statuses without them (``status`` returns the full record) and at
    most ``PAGE_SIZE`` entries; ``next_offset`` continues the listing.
    """

    operations: tuple[OperationStatus, ...] = Field(max_length=PAGE_SIZE)
    next_offset: int | None = Field(default=None, ge=0)
    active: int = Field(ge=0)
    outstanding: int = Field(
        default=0,
        ge=0,
        description="Interrupted rows: the backend may still hold their jobs.",
    )


@final
class OperationLog(Contract):
    """One offset-addressed slice of an operation's bounded log."""

    operation_id: Identifier
    offset: int = Field(ge=0)
    next_offset: int = Field(ge=0)
    truncated: bool
    text: str = Field(max_length=MAX_LOG_CHUNK_BYTES)


def operation_descriptor(
    *,
    id: str,
    version: str,
    title: str,
    description: str,
    input_schema: dict[str, JsonValue],
    result_schema: dict[str, JsonValue],
) -> Descriptor:
    """Build the public descriptor of an operation capability.

    The public input is :class:`OperationCall`; ``input_schema`` describes the
    ``start`` and ``plan`` input and ``result_schema`` the ``result`` object of
    a succeeded status. Both are embedded so a caller that has never heard of
    the operation can validate what it sends and receives.
    """
    input_body = dict(input_schema)
    call_definitions: dict[str, JsonValue] = {}
    _hoist_definitions(input_body, call_definitions)
    identifier: dict[str, JsonValue] = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[a-zA-Z0-9._:@-]+$",
    }
    offset: dict[str, JsonValue] = {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_OFFSET,
    }

    def verb(op: str, required: list[str], **extra: JsonValue) -> dict[str, JsonValue]:
        properties: dict[str, JsonValue] = {"op": {"const": op}, **extra}
        return {
            "type": "object",
            "properties": properties,
            "required": ["op", *required],
            "additionalProperties": False,
        }

    # The verb envelope is spelled out per verb so an external validator sees
    # exactly what each verb requires; the model validator enforces the same.
    call_schema: dict[str, JsonValue] = {
        "title": "OperationCall",
        "oneOf": [
            verb("start", ["input"], input=input_body, operation_id=identifier),
            verb("plan", ["input"], input=input_body),
            verb("status", ["operation_id"], operation_id=identifier),
            verb("cancel", ["operation_id"], operation_id=identifier),
            verb("log", ["operation_id"], operation_id=identifier, offset=offset),
            verb("list", [], offset=offset),
        ],
    }
    if call_definitions:
        call_schema["$defs"] = call_definitions
    status_schema = OperationStatus.model_json_schema()
    result_body = dict(result_schema)
    status_schema["properties"]["result"] = {
        "anyOf": [result_body, {"type": "null"}],
        "default": None,
    }
    # Each model schema carries its own top-level ``$defs``; nesting them
    # under ``oneOf`` would leave their ``#/$defs/...`` references dangling.
    # Hoist every definition to the composed schema's root, where the local
    # references resolve, and strip the nested copies.
    definitions: dict[str, JsonValue] = {}
    # The caller's result schema is embedded inside the status variant, so
    # its definitions are hoisted to the same root as the model definitions.
    _hoist_definitions(result_body, definitions)
    variants: list[JsonValue] = []
    for variant in (
        status_schema,
        OperationPage.model_json_schema(),
        OperationLog.model_json_schema(),
        OperationPlan.model_json_schema(),
    ):
        _hoist_definitions(variant, definitions)
        variants.append(variant)
    if "OperationStatus" in definitions:
        # The page embeds the status by reference; keep the reference target
        # identical to the composed status variant, result schema included.
        definitions["OperationStatus"] = status_schema
    output_schema: dict[str, JsonValue] = {"oneOf": variants}
    if definitions:
        output_schema["$defs"] = definitions
    return Descriptor(
        id=id,
        version=version,
        title=title,
        description=description,
        input_schema=call_schema,
        output_schema=output_schema,
        annotations={OPERATION_ANNOTATION: "1"},
    )


def _hoist_definitions(
    schema: dict[str, JsonValue], definitions: dict[str, JsonValue]
) -> None:
    """Move a schema's ``$defs`` into ``definitions``, refusing conflicts.

    Local references (``#/$defs/...``) target the document root, so every
    schema embedded below the root must surrender its definitions to it.
    """
    nested = schema.pop("$defs", None)
    if not isinstance(nested, dict):
        return
    for name, definition in cast("dict[str, JsonValue]", nested).items():
        if name in definitions and definitions[name] != definition:
            raise ValueError(f"conflicting schema definition {name}")
        definitions[name] = definition


def input_digest(scope: str, payload: dict[str, JsonValue]) -> str:
    """Digest canonical input under the installed node and descriptor scope."""
    encoded = json.dumps(
        {"scope": scope, "input": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class SubmissionRefused(Exception):
    """``submit`` refused the job before any request left the child.

    The message is published as the operation's error, so it must be
    operator-safe. Raising it records a definite failure; any other exception
    from ``submit`` records an uncertain submission, because the request may
    have reached the backend.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason[:256])


class JobAdapter(Protocol):
    """Backend-specific submit, observe and cancel of one job.

    ``submit`` returns the backend reference once the backend has accepted;
    raising :class:`SubmissionRefused` before anything is sent records a
    failure with its reason, and raising anything else after the request went
    out leaves the submission uncertain and the journal records that instead
    of retrying. ``observe`` returns
    ``(state, progress, stage, result, error)`` for a reference; ``cancel``
    asks the backend to stop the job it owns.

    ``stage`` and ``error`` are published verbatim in the operation's status,
    so the adapter must return operator-safe text: a fixed reason or a
    redacted message, never a provider response body, URL, or request
    detail. The same rule already governs every log line the adapter's owner
    appends; the runner cannot redact what it does not understand.
    """

    async def submit(self, operation_id: str, payload: dict[str, JsonValue]) -> str: ...

    async def observe(
        self, reference: str
    ) -> tuple[
        OperationState,
        float | None,
        str | None,
        dict[str, JsonValue] | None,
        str | None,
    ]: ...

    async def cancel(self, reference: str) -> None:
        """Ask the backend to stop the job; return once the request is accepted.

        Acceptance is not termination: the runner keeps observing until
        ``observe`` reports a terminal state, so a backend that stops jobs
        asynchronously is never recorded as stopped before it is.
        """
        ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations (
    key TEXT PRIMARY KEY,
    input_digest TEXT NOT NULL,
    reserved_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    key TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    state TEXT NOT NULL,
    submission TEXT NOT NULL,
    progress REAL,
    stage TEXT,
    submitted_at INTEGER NOT NULL,
    started_at INTEGER,
    updated_at INTEGER NOT NULL,
    finished_at INTEGER,
    input_digest TEXT NOT NULL,
    input_json TEXT NOT NULL,
    backend_reference TEXT,
    result_json TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_sequence ON operations(sequence);
"""

_ACTIVE: tuple[OperationState, ...] = ("queued", "running")


class ResultRefused(ValueError):
    """A terminal result the journal will not persist; the message is safe to show.

    Raised for an oversized result and for one carrying a non-finite number:
    the wire encodes canonically without NaN or infinity, so a record holding
    one could never be served again.
    """


def journal_key(scope: str, operation_id: str) -> str:
    """Row key: the descriptor scope and the caller's operation id together.

    Two descriptors sharing a journal may receive the same caller id; keying
    by both keeps their reservations, records, and logs apart.
    """
    return f"{scope}\x1f{operation_id}"


@final
class OperationJournal:
    """Durable operation records with reservation-scoped idempotency.

    Rows are keyed by scope plus operation id. ``reservations`` is the replay
    fence and is bounded only by its capacity, which refuses new starts when
    full rather than forgetting ids. ``operations`` is the display history
    bounded by ``retain``; pruning it cannot revive a key because ``reserve``
    consults the fence first, and rows that may still own a backend job
    (uncertain submissions, interrupted rows) are never pruned.
    """

    def __init__(
        self,
        path: Path,
        bounds: OperationBounds,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.bounds = bounds
        self.clock = clock
        self.database = Database(path, _SCHEMA)
        self.sequence = 0
        self.retired: list[tuple[str, str]] = []
        with self.database.transaction() as connection:
            row = connection.execute("SELECT MAX(sequence) FROM operations").fetchone()
            self.sequence = int(row[0] or 0)
            # A row that never reached the backend cannot be observed; it
            # fails with a clear reason and is never replayed under its id.
            connection.execute(
                "UPDATE operations SET state = 'failed', finished_at = ?, "
                "updated_at = ?, error = 'interrupted before submission' "
                "WHERE state IN ('queued', 'running') AND submission = 'pending'",
                (self.clock(), self.clock()),
            )
            # Uncertain (the submit call was in flight) and accepted rows may
            # have a job on the backend: they become interrupted and are kept.
            connection.execute(
                "UPDATE operations SET state = 'interrupted', updated_at = ? "
                "WHERE state IN ('queued', 'running')",
                (self.clock(),),
            )

    def reserve(
        self,
        operation_id: str,
        digest: str,
        payload: dict[str, JsonValue],
        scope: str = "",
    ) -> tuple[Literal["created", "existing"], OperationStatus]:
        """Reserve an id atomically or return the existing matching record.

        Raises ``ValueError`` when the id was reserved under a different
        digest, when the queue or the fence is full, or when the id is
        reserved but its display row has been pruned (the fence still holds).
        """
        now = self.clock()
        key = journal_key(scope, operation_id)
        with self.database.transaction() as connection:
            fence = connection.execute(
                "SELECT input_digest FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            if fence is not None:
                if fence[0] != digest:
                    raise ValueError("operation id is reserved under a different input")
                existing = self._read(connection, key)
                if existing is None:
                    raise ValueError("operation id was already used")
                return "existing", existing
            queued = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE state = 'queued'"
            ).fetchone()[0]
            if int(queued) >= self.bounds.max_queued:
                raise ValueError("operation queue is full")
            reserved = connection.execute(
                "SELECT COUNT(*) FROM reservations"
            ).fetchone()[0]
            if int(reserved) >= self.bounds.reservations:
                # Fail closed: forgetting an old reservation would make its
                # id replayable, so a full fence refuses new work instead.
                raise ValueError(
                    "operation journal is full; retire it before new starts"
                )
            self.sequence += 1
            connection.execute(
                "INSERT INTO reservations VALUES (?, ?, ?)", (key, digest, now)
            )
            connection.execute(
                "INSERT INTO operations (key, operation_id, scope, state, submission, "
                "progress, stage, submitted_at, started_at, updated_at, finished_at, "
                "input_digest, input_json, backend_reference, result_json, error, "
                "cancel_requested, sequence) VALUES (?, ?, ?, 'queued', 'pending', "
                "NULL, NULL, ?, NULL, ?, NULL, ?, ?, NULL, NULL, NULL, 0, ?)",
                (
                    key,
                    operation_id,
                    scope,
                    now,
                    now,
                    digest,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    self.sequence,
                ),
            )
            self._prune(connection)
            created = self._read(connection, key)
        assert created is not None
        return "created", created

    def update(
        self,
        operation_id: str,
        *,
        scope: str = "",
        state: OperationState | None = None,
        submission: Submission | None = None,
        progress: float | None = None,
        stage: str | None = None,
        backend_reference: str | None = None,
        result: dict[str, JsonValue] | None = None,
        error: str | None = None,
    ) -> OperationStatus:
        """Record one observation; terminal states stamp ``finished_at``.

        Raises ``ValueError`` for an unknown row, a result past the bound, a
        reference past the bound, or a value the status contract refuses.
        """
        now = self.clock()
        key = journal_key(scope, operation_id)
        assignments: list[str] = ["updated_at = ?"]
        values: list[object] = [now]
        if state is not None:
            assignments.append("state = ?")
            values.append(state)
            if state == "running":
                # The runtime deadline is anchored to the first running
                # transition and survives restarts and reconciliation. A row
                # returning to running (reconciliation) is not finished.
                assignments.append("started_at = COALESCE(started_at, ?)")
                values.append(now)
                assignments.append("finished_at = NULL")
            if state in ("succeeded", "failed", "cancelled"):
                # Only a genuinely terminal state is finished; interrupted
                # rows may still own backend work and are reconciled.
                assignments.append("finished_at = ?")
                values.append(now)
        if submission is not None:
            assignments.append("submission = ?")
            values.append(submission)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(progress)
        if stage is not None:
            assignments.append("stage = ?")
            values.append(stage)
        if backend_reference is not None:
            if len(backend_reference) > MAX_REFERENCE_LENGTH:
                # Never store a different identity than the backend's own.
                raise ValueError("backend reference exceeds the bound")
            assignments.append("backend_reference = ?")
            values.append(backend_reference)
        if result is not None:
            try:
                encoded = json.dumps(
                    result, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            except ValueError as encoding_error:
                raise ResultRefused(
                    "operation result holds a non-finite number"
                ) from encoding_error
            if len(encoded.encode()) > MAX_RESULT_BYTES:
                raise ResultRefused("operation result exceeds the bound")
            assignments.append("result_json = ?")
            values.append(encoded)
        if error is not None or (state is not None and state not in _ACTIVE):
            # A terminal transition always writes the error column, so a row
            # that was interrupted with a worker error and later reconciles to
            # success does not keep the obsolete failure text.
            assignments.append("error = ?")
            values.append(None if error is None else error[:256])
        values.append(key)
        with self.database.transaction() as connection:
            before = self._read(connection, key)
            if before is None:
                raise ValueError("unknown operation")
            statement = f"UPDATE operations SET {', '.join(assignments)} WHERE key = ?"
            connection.execute(statement, values)
            try:
                updated = self._read(connection, key)
            except ValueError:
                # The contract refused a value (a progress outside 0..1, an
                # overlong stage): roll the write back and report it.
                raise
        if updated is None:
            raise ValueError("unknown operation")
        return updated

    def status(self, operation_id: str, scope: str = "") -> OperationStatus | None:
        """Return one retained record, or ``None``."""
        with self.database.transaction() as connection:
            return self._read(connection, journal_key(scope, operation_id))

    def started_at(self, operation_id: str, scope: str = "") -> int | None:
        """Return the persisted first running transition, if any."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT started_at FROM operations WHERE key = ?",
                (journal_key(scope, operation_id),),
            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def payload(
        self, operation_id: str, scope: str = ""
    ) -> dict[str, JsonValue] | None:
        """Return the recorded input of one operation for the worker."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT input_json FROM operations WHERE key = ?",
                (journal_key(scope, operation_id),),
            ).fetchone()
        if row is None:
            return None
        loaded: dict[str, JsonValue] = json.loads(row[0])
        return loaded

    def page(self, scope: str = "", offset: int = 0) -> OperationPage:
        """Return one page of a scope's records, newest first, compacted.

        Results and backend references are omitted so the page provably fits
        one frame; ``status`` returns the full record.

        ``active`` and ``outstanding`` count the whole scope, not the page.
        """
        with self.database.transaction() as connection:
            keys = connection.execute(
                "SELECT key FROM operations WHERE scope = ? ORDER BY sequence DESC "
                "LIMIT ? OFFSET ?",
                (scope, PAGE_SIZE + 1, offset),
            ).fetchall()
            items = [self._read(connection, row[0]) for row in keys[:PAGE_SIZE]]
            active = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE scope = ? "
                "AND state IN ('queued', 'running')",
                (scope,),
            ).fetchone()[0]
            outstanding = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE scope = ? "
                "AND state = 'interrupted'",
                (scope,),
            ).fetchone()[0]
        operations = tuple(
            item.model_copy(
                update={"result": None, "backend_reference": None, "input": None}
            )
            for item in items
            if item is not None
        )
        return OperationPage(
            operations=operations,
            next_offset=offset + PAGE_SIZE if len(keys) > PAGE_SIZE else None,
            active=int(active),
            outstanding=int(outstanding),
        )

    def request_cancel(self, operation_id: str, scope: str = "") -> None:
        """Persist a cancel intent so it survives a restart before it is honored."""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE operations SET cancel_requested = 1, updated_at = ? "
                "WHERE key = ?",
                (self.clock(), journal_key(scope, operation_id)),
            )

    def cancel_requested(self, operation_id: str, scope: str = "") -> bool:
        """Whether a persisted cancel intent is outstanding for the row."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM operations WHERE key = ?",
                (journal_key(scope, operation_id),),
            ).fetchone()
        return row is not None and bool(row[0])

    def next_queued(self, scope: str = "") -> str | None:
        """Return the oldest queued operation id in ``scope``."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id FROM operations WHERE state = 'queued' "
                "AND scope = ? ORDER BY sequence ASC LIMIT 1",
                (scope,),
            ).fetchone()
        return None if row is None else str(row[0])

    def interrupted_accepted(self, scope: str = "") -> list[tuple[str, str]]:
        """Return interrupted rows in ``scope`` the backend accepted: id, reference."""
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT operation_id, backend_reference FROM operations WHERE "
                "state = 'interrupted' AND submission = 'accepted' AND "
                "backend_reference IS NOT NULL AND scope = ? ORDER BY sequence ASC",
                (scope,),
            ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def drain_retired(self) -> list[tuple[str, str]]:
        """Return and clear the ``(scope, operation_id)`` pairs pruned so far."""
        retired, self.retired = self.retired, []
        return retired

    def _prune(self, connection: object) -> None:
        import sqlite3

        assert isinstance(connection, sqlite3.Connection)
        # Rows that may still own a backend job (uncertain submissions and
        # interrupted rows) are never pruned: they are the only record that
        # ownership or cleanup reconciliation can start from.
        predicate = (
            "state NOT IN ('queued', 'running', 'interrupted') "
            "AND submission != 'uncertain' "
            "AND key NOT IN (SELECT key FROM operations ORDER BY sequence DESC LIMIT ?)"
        )
        pruned = connection.execute(
            f"SELECT scope, operation_id FROM operations WHERE {predicate}",
            (self.bounds.retain,),
        ).fetchall()
        connection.execute(
            f"DELETE FROM operations WHERE {predicate}", (self.bounds.retain,)
        )
        for row in pruned:
            self.retired.append((str(row[0]), str(row[1])))

    def _read(self, connection: object, key: str) -> OperationStatus | None:
        import sqlite3

        assert isinstance(connection, sqlite3.Connection)
        row = connection.execute(
            "SELECT operation_id, state, submission, progress, stage, submitted_at, "
            "updated_at, finished_at, input_digest, backend_reference, result_json, "
            "error, scope, input_json FROM operations WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        now = self.clock()
        updated_at = int(row[6])
        stale = row[1] in _ACTIVE and now - updated_at > self.bounds.stale_after_seconds
        queue_position: int | None = None
        if row[1] == "queued":
            # Each scope has its own runner, so only that scope's queue is
            # ahead of the row.
            ahead = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE state = 'queued' AND scope = ? "
                "AND sequence < (SELECT sequence FROM operations WHERE key = ?)",
                (row[12], key),
            ).fetchone()
            queue_position = int(ahead[0])
        return OperationStatus(
            operation_id=row[0],
            state=row[1],
            submission=row[2],
            progress=row[3],
            stage=row[4],
            queue_position=queue_position,
            submitted_at=int(row[5]),
            updated_at=updated_at,
            finished_at=None if row[7] is None else int(row[7]),
            stale=stale,
            input_digest=row[8],
            backend_reference=row[9],
            input=_echoed_input(row[13]),
            result=None if row[10] is None else json.loads(row[10]),
            error=row[11],
        )


def _echoed_input(stored: object) -> dict[str, JsonValue] | None:
    """The recorded input when it is small enough to ride a status reply."""
    if not isinstance(stored, str) or len(stored.encode()) > MAX_INPUT_ECHO_BYTES:
        return None
    loaded: dict[str, JsonValue] = json.loads(stored)
    return loaded


@final
class OperationLogs:
    """Bounded per-operation log files the child owns, keyed by scope and id."""

    def __init__(self, directory: Path, bounds: OperationBounds) -> None:
        self.directory = directory
        self.bounds = bounds
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, operation_id: str, scope: str) -> Path:
        # A digest keeps distinct keys distinct: a character map would fold
        # ``a:b`` and ``a@b`` onto one file.
        digest = hashlib.sha256(journal_key(scope, operation_id).encode()).hexdigest()
        return self.directory / f"{digest[:32]}.log"

    def append(self, operation_id: str, line: str, scope: str = "") -> None:
        """Append one already-redacted line; drop lines past the byte bound."""
        path = self._path(operation_id, scope)
        encoded = (line.rstrip("\n") + "\n").encode()
        # Logging is evidence, never authority: a line that cannot be written
        # (full disk, lost directory) must not fail the operation it describes.
        try:
            # The entry is opened without following links, so a planted link
            # under a predictable log name cannot redirect the append.
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            try:
                if os.fstat(descriptor).st_size + len(encoded) > self.bounds.log_bytes:
                    return
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
        except OSError:
            return

    def retire(self, operation_id: str, scope: str = "") -> None:
        """Delete the log of an operation pruned from history."""
        path = self._path(operation_id, scope)
        if path.exists():
            path.unlink()

    def read(self, operation_id: str, offset: int, scope: str = "") -> OperationLog:
        """Return one bounded slice starting at ``offset``."""
        path = self._path(operation_id, scope)
        if not path.exists():
            return OperationLog(
                operation_id=operation_id,
                offset=offset,
                next_offset=offset,
                truncated=False,
                text="",
            )
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            # A link (or a vanished file) reads as an empty log.
            return OperationLog(
                operation_id=operation_id,
                offset=offset,
                next_offset=offset,
                truncated=False,
                text="",
            )
        with os.fdopen(descriptor, "rb") as source:
            source.seek(offset)
            chunk = source.read(MAX_LOG_CHUNK_BYTES)
            remaining = source.read(1)
        text = chunk.decode("utf-8", errors="replace")
        return OperationLog(
            operation_id=operation_id,
            offset=offset,
            next_offset=offset + len(chunk),
            truncated=bool(remaining),
            text=text,
        )


@final
class OperationRunner:
    """One worker draining the queue through a backend adapter.

    The worker submits, observes until terminal, and records every step in
    the journal. A runner owns one descriptor ``scope``: it dequeues and
    reconciles only that scope's rows, so descriptors sharing a journal each
    run through their own adapter and result schema. Cancellation asks the
    adapter; the runner owns no process group and kills nothing.
    ``max_runtime_seconds`` cancels through the same adapter path, anchored to
    the persisted first running transition. Every adapter await is bounded by
    ``adapter_seconds``.
    """

    def __init__(
        self,
        journal: OperationJournal,
        logs: OperationLogs,
        adapter: JobAdapter,
        *,
        scope: str = "",
        poll_seconds: float = 1.0,
        adapter_seconds: float = 30.0,
        result_schema: dict[str, JsonValue],
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.journal = journal
        self.logs = logs
        self.adapter = adapter
        self.scope = scope
        self.poll_seconds = poll_seconds
        self.adapter_seconds = adapter_seconds
        # A succeeded result is validated against the descriptor's result
        # schema before it is persisted; a mismatch is a safe failure, never a
        # success the descriptor would then refuse to serve. The schema is
        # required so no runner can persist what its descriptor refuses.
        self.result_schema = result_schema
        self.clock = clock
        self.wake = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.current: str | None = None
        self.overdue_retry: dict[str, int] = {}
        """Per operation: the clock time before which an overdue outstanding row
        is not re-observed. Kept per row so one job that never answers cannot
        hold every other parked job's next observation."""

    def start(self) -> None:
        """Start the single worker task."""
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the worker; active rows stay as they are for reconciliation."""
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def enqueue(self) -> None:
        """Wake the worker after a reservation."""
        self.wake.set()

    def request_cancel(self, operation_id: str) -> OperationStatus:
        """Cancel a queued operation now or ask the adapter for a running one."""
        scope = self.scope
        status = self.journal.status(operation_id, scope)
        if status is None:
            raise ValueError("unknown operation")
        if status.state == "queued":
            return self.journal.update(operation_id, scope=scope, state="cancelled")
        if status.state == "running" or (
            status.state == "interrupted" and status.backend_reference is not None
        ):
            # The intent is persisted before anything else: a restart between
            # this request and the adapter call must not lose it. An
            # interrupted accepted job is cancelled through reconciliation.
            self.journal.request_cancel(operation_id, scope)
            self.wake.set()
        return status

    async def _run(self) -> None:
        while True:
            # Ownership before new work: every interrupted row the backend
            # accepted (after a restart, or after a local fault in this
            # process) is observed through its stored reference, never
            # resubmitted, before another queued row can start paid work.
            interrupted = self.journal.interrupted_accepted(self.scope)
            for operation_id, reference in interrupted:
                if self.clock() < self.overdue_retry.get(operation_id, 0):
                    continue
                self.overdue_retry.pop(operation_id, None)
                await self._execute(operation_id, reference)
            if interrupted and self._blocking_interrupted():
                # A row that faulted again stays owned; retry after a pause
                # rather than spinning or abandoning it for the queue.
                await asyncio.sleep(self.poll_seconds)
                continue
            queued = self.journal.next_queued(self.scope)
            if queued is None:
                self.wake.clear()
                if self.journal.interrupted_accepted(self.scope):
                    # Only overdue rows remain outstanding: the queue is not
                    # gated on them, but they are observed again on a timer
                    # so a backend that finally answers is not missed.
                    try:
                        await asyncio.wait_for(self.wake.wait(), OVERDUE_RETRY_SECONDS)
                    except TimeoutError:
                        # Every parked row is due again on the timer.
                        self.overdue_retry.clear()
                else:
                    await self.wake.wait()
                continue
            await self._execute(queued)

    def _blocking_interrupted(self) -> bool:
        """Whether an interrupted row still gates the queue.

        A row the runtime bound condemned and the backend stopped answering
        for stays outstanding (observed again on the retry timer) but no
        longer holds every later render hostage; every other interrupted
        row is reconciled before new work, as before.
        """
        for operation_id, _ in self.journal.interrupted_accepted(self.scope):
            status = self.journal.status(operation_id, self.scope)
            if status is not None and status.error != OVERDUE_ERROR:
                return True
        return False

    def _result_matches(self, result: dict[str, JsonValue] | None) -> bool:
        """Whether a succeeded result satisfies the descriptor's result schema."""
        if result is None:
            return False
        try:
            validate_schema(result, self.result_schema)
        except SchemaValidationError:
            return False
        return True

    def _retire_logs(self) -> None:
        for scope, operation_id in self.journal.drain_retired():
            self.logs.retire(operation_id, scope)

    async def _execute(self, operation_id: str, reference: str | None = None) -> None:
        scope = self.scope
        try:
            await self._execute_guarded(operation_id, reference)
        except asyncio.CancelledError:
            # The runner is stopping. A running row keeps its ownership as
            # interrupted: an accepted one is reconciled by any later runner on
            # this journal, and one whose submission was in flight is recorded
            # as outstanding rather than left running and stale.
            try:
                status = self.journal.status(operation_id, scope)
                if status is not None and status.state == "running":
                    self.journal.update(
                        operation_id,
                        scope=scope,
                        state="interrupted",
                        error=None
                        if status.backend_reference is not None
                        else "backend submission uncertain",
                    )
            except Exception:
                pass
            raise
        except Exception as error:
            # The worker must outlive any single operation: record the failure
            # with a safe error and move on to the next queued row. A row the
            # backend already accepted still owns a job there, so it becomes
            # interrupted (reconciled on the next start) rather than failed.
            self.logs.append(
                operation_id, f"worker failure: {type(error).__name__}", scope
            )
            try:
                status = self.journal.status(operation_id, scope)
                # An uncertain submission may hold a job too (the reference
                # may have been returned and lost to the fault), so it stays
                # outstanding rather than failed.
                owned = status is not None and (
                    status.backend_reference is not None
                    or status.submission == "uncertain"
                )
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="interrupted" if owned else "failed",
                    error="operation worker failure",
                )
            except Exception:
                pass
        finally:
            self.current = None
            self._retire_logs()

    async def _execute_guarded(
        self, operation_id: str, reference: str | None = None
    ) -> None:
        scope = self.scope
        self.current = journal_key(scope, operation_id)
        if reference is None:
            payload = self.journal.payload(operation_id, scope)
            if payload is None:
                return
            # Persist the uncertainty before the external call: if the
            # process dies while submit() is in flight, the row must say the
            # backend may hold the job rather than "never submitted".
            self.journal.update(
                operation_id,
                scope=scope,
                state="running",
                submission="uncertain",
                stage="submitting",
            )
            try:
                async with asyncio.timeout(self.adapter_seconds):
                    reference = await self.adapter.submit(operation_id, payload)
            except SubmissionRefused as refusal:
                # Nothing left the child, so the row is a plain failure with
                # the adapter's operator-safe reason; nothing to reconcile.
                self.logs.append(operation_id, f"submit refused: {refusal}", scope)
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="failed",
                    submission="pending",
                    error=str(refusal),
                )
                return
            except Exception as error:
                # The request may or may not have reached the backend; record
                # the uncertainty instead of retrying, and leave observation
                # to a later reconcile. The row is retained for that.
                self.logs.append(
                    operation_id, f"submit failed: {type(error).__name__}", scope
                )
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="interrupted",
                    error="backend submission uncertain",
                )
                return
            if len(reference) > MAX_REFERENCE_LENGTH:
                # A reference we cannot store exactly is one we cannot own
                # after a restart; the job may run, so the row stays uncertain.
                self.logs.append(
                    operation_id, "backend reference exceeds the bound", scope
                )
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="interrupted",
                    error="backend reference exceeds the bound",
                )
                return
            self.journal.update(
                operation_id,
                scope=scope,
                submission="accepted",
                backend_reference=reference,
                stage="accepted",
            )
            self.logs.append(operation_id, "accepted by backend", scope)
        else:
            # Reconciliation of an accepted job after a restart: the row
            # returns to running under observation with its original deadline.
            self.journal.update(
                operation_id, scope=scope, state="running", stage="reconciling"
            )
            self.logs.append(operation_id, "reconciling accepted job", scope)
        started = self.journal.started_at(operation_id, scope)
        if started is None:
            started = self.clock()
        # Set once the backend accepted a cancel: the row stays running and
        # observed until the backend itself reports a terminal state, because
        # an acknowledged cancel is a request, not proof the job stopped.
        cancel_sent: str | None = None
        limit = self.journal.bounds.max_runtime_seconds

        def wait_seconds(overdue: bool) -> float:
            # Before the bound a call may not outlive the remaining runtime,
            # so a hung backend cannot hold the deadline check past it. Once
            # the loop has entered the overdue path (the cancel is sent on
            # that same pass) each observation gets the adapter's full
            # window: a job that finished at the bound may still need its
            # result collected, and the parking rule bounds how many such
            # windows a silent backend can take.
            if overdue:
                return self.adapter_seconds
            remaining = float(limit - (self.clock() - started))
            return min(self.adapter_seconds, max(remaining, MIN_WAIT_SECONDS))

        observe_failures = 0
        while True:
            requested = self.journal.cancel_requested(operation_id, scope)
            overdue = self.clock() - started >= limit
            cancel_failed = False
            if (requested or overdue) and cancel_sent is None:
                try:
                    async with asyncio.timeout(wait_seconds(overdue)):
                        await self.adapter.cancel(reference)
                except Exception as error:
                    # The backend still holds the job: keep the row running so
                    # nobody treats it as stopped, retry on the next pass, and
                    # still observe it below. A backend that refuses to cancel
                    # a job that just finished must be allowed to report that
                    # terminal state, or the scope's worker would never drain.
                    cancel_failed = True
                    self.logs.append(
                        operation_id, f"cancel failed: {type(error).__name__}", scope
                    )
                else:
                    cancel_sent = (
                        "cancel requested" if requested else "runtime bound exceeded"
                    )
                    self.journal.update(operation_id, scope=scope, stage="cancelling")
                    self.logs.append(operation_id, "cancel accepted by backend", scope)
            try:
                async with asyncio.timeout(wait_seconds(overdue)):
                    state, progress, stage, result, error = await self.adapter.observe(
                        reference
                    )
            except Exception as error:
                self.logs.append(
                    operation_id, f"observe failed: {type(error).__name__}", scope
                )
                observe_failures += 1
                if overdue and observe_failures >= OVERDUE_OBSERVE_FAILURES:
                    # Past the runtime bound the backend has been asked to
                    # stop and no longer answers: the worker moves on, but
                    # the job was never confirmed stopped, so the row stays
                    # interrupted and outstanding with its reference, to be
                    # observed again later rather than forgotten.
                    self.journal.update(
                        operation_id,
                        scope=scope,
                        state="interrupted",
                        stage="overdue",
                        error=OVERDUE_ERROR,
                    )
                    self.overdue_retry[operation_id] = self.clock() + int(
                        OVERDUE_RETRY_SECONDS
                    )
                    return
                await asyncio.sleep(self.poll_seconds)
                continue
            observe_failures = 0
            if state in _ACTIVE or state == "interrupted":
                # An adapter reporting interrupted is describing the backend's
                # own uncertainty; the job is not over, so keep observing. A
                # malformed observation (a progress outside 0..1, an overlong
                # stage) is dropped and observation continues: the owned job
                # must never be terminalized by a bad report.
                try:
                    self.journal.update(
                        operation_id,
                        scope=scope,
                        state="running",
                        progress=progress,
                        stage="cancel failed"
                        if cancel_failed
                        else ("cancelling" if cancel_sent else stage),
                    )
                except ValueError:
                    self.logs.append(
                        operation_id, "malformed observation dropped", scope
                    )
                await asyncio.sleep(self.poll_seconds)
                continue
            if state == "succeeded" and not self._result_matches(result):
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="failed",
                    stage=stage,
                    error="operation result does not match its schema",
                )
                self.logs.append(
                    operation_id, "finished: failed (result schema)", scope
                )
                break
            if state == "cancelled" and error is None and cancel_sent is not None:
                # The backend confirmed our cancel; say why it was asked for.
                error = None if cancel_sent == "cancel requested" else cancel_sent
            try:
                # Only a succeeded operation has a result the descriptor
                # constrains; a diagnostic payload on failure or cancellation
                # is not persisted, so status never fails output validation.
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state=state,
                    progress=1.0 if state == "succeeded" else progress,
                    stage=stage,
                    result=result if state == "succeeded" else None,
                    error=error,
                )
            except ValueError as error:
                # The terminal report itself was unusable (a refused result or
                # a malformed field). The job is over on the backend, so record
                # a safe failure rather than a false success; the worker lives.
                self.journal.update(
                    operation_id,
                    scope=scope,
                    state="failed",
                    error=str(error)
                    if isinstance(error, ResultRefused)
                    else "operation result is unusable",
                )
                self.logs.append(
                    operation_id, "finished: failed (unusable result)", scope
                )
                break
            self.logs.append(operation_id, f"finished: {state}", scope)
            break


@final
class OperationService:
    """Verb dispatcher for one operation descriptor under explicit authority.

    ``authority`` is decided by the caller from its own grant: ``read`` may
    observe (``status``, ``list``, ``log``); ``effect`` may also ``start``,
    ``cancel`` and ``plan``. The dispatcher validates the verb against the
    authority before touching the journal, so exposing reads never exposes
    starts. ``scope`` names the descriptor (installed node plus identity);
    every row is keyed under it, so services sharing one journal cannot see
    or stop each other's operations.
    """

    def __init__(
        self,
        *,
        scope: str,
        journal: OperationJournal,
        logs: OperationLogs,
        runner: OperationRunner,
        plan: Callable[[dict[str, JsonValue]], Awaitable[OperationPlan]],
    ) -> None:
        if runner.scope != scope:
            # A runner submits through one adapter and validates against one
            # result schema; a service must never enqueue into another's.
            raise ValueError("operation runner does not own the service scope")
        self.scope = scope
        self.journal = journal
        self.logs = logs
        self.runner = runner
        self.plan = plan

    async def dispatch(self, call: OperationCall, authority: Authority) -> Contract:
        """Run one verb and return its typed result."""
        if call.op in ("start", "cancel", "plan") and authority != "effect":
            raise PermissionError(f"{call.op} requires effect authority")
        if call.op == "list":
            return self.journal.page(self.scope, call.offset or 0)
        if call.op == "status":
            assert call.operation_id is not None
            status = self.journal.status(call.operation_id, self.scope)
            if status is None:
                raise ValueError("unknown operation")
            return status
        if call.op == "log":
            assert call.operation_id is not None
            if self.journal.status(call.operation_id, self.scope) is None:
                raise ValueError("unknown operation")
            return self.logs.read(call.operation_id, call.offset or 0, self.scope)
        if call.op == "plan":
            assert call.input is not None
            return await self.plan(call.input)
        if call.op == "cancel":
            assert call.operation_id is not None
            return self.runner.request_cancel(call.operation_id)
        assert call.input is not None
        operation_id = (
            call.operation_id
            or hashlib.sha256(
                input_digest(self.scope, call.input).encode()
            ).hexdigest()[:32]
        )
        outcome, status = self.journal.reserve(
            operation_id, input_digest(self.scope, call.input), call.input, self.scope
        )
        if outcome == "created":
            self.runner.enqueue()
        return status
