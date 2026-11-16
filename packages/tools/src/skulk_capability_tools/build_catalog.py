"""Publisher-side catalog builder: list the releases this key signed, sign the list."""

import argparse
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skulk_capability_sdk.contracts import canonical
from skulk_capability_sdk.storage import atomic_private, read_private

from .catalog import (
    CATALOG_MAX_BYTES,
    Catalog,
    CatalogEntry,
    SignedCatalog,
    entry_from_record,
)
from .native_catalog import (
    NativeCatalog,
    NativeCatalogEntry,
    SignedNativeCatalog,
    native_catalog_message,
    native_entry_from_record,
)
from .native_publishing import SignedNativeRuntimeRelease
from .publication_records import (
    publication_message,
    publication_release,
    read_publication_record,
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
        "--native",
        action="store_true",
        help="Select the separately signed catalog-four contract",
    )
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
    entries: list[CatalogEntry | NativeCatalogEntry] = []
    for directory, feed_url in args.release:
        raw = read_private(Path(directory) / "release.json", 1048576)
        record = read_publication_record(raw)
        release = publication_release(record)
        # A catalog lists only what this key signed: a record signed by
        # someone else, or altered since, is refused before it is listed.
        if release.publisher != args.publisher:
            parser.error(f"{directory}: release publisher differs from the catalog's")
        try:
            public.verify(bytes.fromhex(record.signature), publication_message(record))
        except (InvalidSignature, ValueError):
            parser.error(f"{directory}: release record is not signed by this key")
        if isinstance(record, SignedNativeRuntimeRelease):
            if not args.native:
                parser.error("native releases require explicit catalog --native")
            entries.append(native_entry_from_record(raw, feed_url))
        else:
            entries.append(entry_from_record(raw, feed_url))
    if args.native:
        native = NativeCatalog(
            publisher=args.publisher,
            revision=args.revision,
            created_at=int(time.time()),
            expires_at=args.expires_at,
            entries=tuple(entries),
        )
        document = (
            SignedNativeCatalog(
                catalog=native,
                signature=key.sign(native_catalog_message(native)).hex(),
            )
            .model_dump_json()
            .encode()
        )
        if len(document) > CATALOG_MAX_BYTES:
            parser.error("native catalog exceeds the supported reader metadata bound")
        atomic_private(args.output, document)
        return
    legacy = tuple(entry for entry in entries if isinstance(entry, CatalogEntry))
    catalog = Catalog(
        protocol=3 if any(len(entry.descriptors) > 8 for entry in entries) else 2,
        publisher=args.publisher,
        revision=args.revision,
        created_at=int(time.time()),
        expires_at=args.expires_at,
        entries=legacy,
    )
    signed = SignedCatalog(
        catalog=catalog, signature=key.sign(canonical(catalog)).hex()
    )
    document = signed.model_dump_json().encode()
    if len(document) > CATALOG_MAX_BYTES:
        parser.error("serialized catalog exceeds the supported reader metadata bound")
    atomic_private(args.output, document)


if __name__ == "__main__":
    main()
