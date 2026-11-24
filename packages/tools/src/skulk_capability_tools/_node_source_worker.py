"""Replace an owned local source process with the explicitly selected Node."""

import argparse
import os
import stat
from pathlib import Path

from pydantic import Field

from skulk_capability_sdk.contracts import Contract, Denied
from skulk_capability_sdk.node_application import (
    MAX_APPLICATION_METADATA,
    NodeApplication,
    launch_node_source,
)


class Selection(Contract):
    """Private local-generation metadata, never a Fabric or provider credential."""

    application: NodeApplication
    directory: str = Field(max_length=4096)


def main() -> None:
    """Consume private source selection once and retain original startup ownership."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-fd", type=int, required=True)
    parser.add_argument("--source-fd", type=int, required=True)
    options = parser.parse_args()
    if options.config_fd == options.source_fd:
        raise Denied("Source and startup descriptors must be distinct")
    with os.fdopen(options.source_fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise Denied("Source selection requires a private regular descriptor")
        raw = stream.read(MAX_APPLICATION_METADATA + 8193)
    if len(raw) > MAX_APPLICATION_METADATA + 8192:
        raise Denied("Source selection exceeds its bound")
    selected = Selection.model_validate_json(raw)
    launch_node_source(
        selected.application, Path(selected.directory), options.config_fd
    )


if __name__ == "__main__":
    main()
