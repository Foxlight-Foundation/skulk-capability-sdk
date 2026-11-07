# Working on the public capability SDK

Keep authoring contracts, shared kernel/bindings and generic author tools public
under MIT. Do not add owner/supervisor implementation, provider account state,
private repository history, credentials or local environment details.

Preserve frozen canonical bytes, descriptor revisions, protocol windows and
journal identities. Native adoption and higher-level interfaces require their
own compatibility and installed acceptance; source presence is not qualification.
Account-free source/package acquisition must remain independent of Foxlight
catalog publishing registration and independent runtime authorization.

Use ready PRs. Before committing implementation, run frozen strict types,
Ruff lint/format, tests, artifact build and whitespace checks. Resolve actionable
review findings before merging. Record exact checked revisions and distinguish
source checks from published artifacts and installed behavior.

For the workspace, sync with `uv sync --frozen --all-packages --no-editable` before final validation, then use `uv run --no-sync` for the checks. This tests real wheel metadata rather than relying on editable distribution import-name discovery. Build with `uv build --all-packages`; run both `tools/check_export.py` and `tools/check_tools_export.py` against the resulting archives. Refresh explicit extraction hashes after an intentional source change; never weaken an allowlist to include owner/provider implementation.
