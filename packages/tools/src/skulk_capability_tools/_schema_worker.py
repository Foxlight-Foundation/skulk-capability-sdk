"""Evaluate local author schemas offline with a bounded CPU and input budget."""

import json
import resource
import sys

from jsonschema import Draft202012Validator, validate
from pydantic import JsonValue
from referencing import Registry


def main() -> None:
    """Validate one local payload without retrieval, logs or retained content."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    raw = sys.stdin.buffer.read(524289)
    if len(raw) > 524288:
        raise SystemExit(1)
    schema, payload = json.loads(raw)
    validate(payload, schema, cls=Draft202012Validator, registry=Registry[JsonValue]())


if __name__ == "__main__":
    main()
