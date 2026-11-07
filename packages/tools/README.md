# Skulk capability author tools

Generic author and publisher tooling for Skulk capabilities, licensed under MIT.
The package provides `skulk-cap` and the existing `capability-build`,
`capability-wheelhouse`, `capability-catalog` and `capability-feed` commands.
It depends on the public author SDK and packages from public package indexes.

Publisher signing keys stay on the publishing host. The independently installed
Skulk owner enforces trust, admission and lifecycle operations. Installing these
tools does not grant runtime authority or access to a provider account.

`EXTRACTION.json` records the reviewed generic source. The private owner,
supervisor, installer and provider implementations are excluded.

Use the [Python author guide](https://github.com/Foxlight-Foundation/skulk-capability-sdk/blob/main/docs/python-authoring.md) for package installation and a complete source-to-host walkthrough. Local development currently exercises unary IPC; the private owner remains independently installed.

The pinned runtime recipes acquire existing public wheel artifacts by exact SHA-256 and size. They include no owner implementation and confer no trust. Independently operated HTTPS feeds need no Foxlight account; hosted catalog submission is a separate service.
