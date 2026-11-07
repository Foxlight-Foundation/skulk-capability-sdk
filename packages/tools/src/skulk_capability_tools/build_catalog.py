"""Publisher-side catalog builder: list the releases this key signed, sign the list."""

import argparse
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skulk_capability_sdk.contracts import canonical
from skulk_capability_sdk.storage import atomic_private, read_private

from .catalog import (
    Catalog,
    CatalogEntry,
    SignedCatalog,
    entry_from_record,
    release_from_record,
    signed_claims,
    signed_record,
)


def main(argv: list[str] | None = None) -> None:
    """Build and sign ``catalog.json`` from release directories and their feed URLs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--expires-at", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--release",
        nargs=2,
        action="append",
        default=[],
        metavar=("DIRECTORY", "FEED_URL"),
        help="a release directory holding release.json, and the HTTPS directory "
        "serving it",
    )
    args = parser.parse_args(argv)
    key = Ed25519PrivateKey.from_private_bytes(read_private(args.private_key, 32))
    public = key.public_key()
    entries: list[CatalogEntry] = []
    for directory, feed_url in args.release:
        raw = read_private(Path(directory) / "release.json", 1048576)
        release = release_from_record(raw)
        # A catalog lists only what this key signed: a record signed by
        # someone else, or altered since, is refused before it is listed.
        if release.publisher != args.publisher:
            parser.error(f"{directory}: release publisher differs from the catalog's")
        record = signed_record(raw)
        try:
            public.verify(
                bytes.fromhex(record.signature), canonical(signed_claims(record))
            )
        except (InvalidSignature, ValueError):
            parser.error(f"{directory}: release record is not signed by this key")
        entries.append(entry_from_record(raw, feed_url))
    catalog = Catalog(
        protocol=3 if any(len(entry.descriptors) > 8 for entry in entries) else 2,
        publisher=args.publisher,
        revision=args.revision,
        created_at=int(time.time()),
        expires_at=args.expires_at,
        entries=tuple(entries),
    )
    signed = SignedCatalog(
        catalog=catalog, signature=key.sign(canonical(catalog)).hex()
    )
    atomic_private(args.output, signed.model_dump_json().encode())


if __name__ == "__main__":
    main()
