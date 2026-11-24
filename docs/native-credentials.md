# Native credential sessions

Applications read credentials declared by their signed manifest through
`ApplicationStartup.credentials`. The owner selects and provisions the storage
backend. Applications cannot select a different store or fall back to another
backend when a selected entry is unavailable.

The existing choices are explicit protected-file storage for headless services,
macOS Keychain and Linux Secret Service. Native storage requires an available OS
session. Installing the SDK or selecting a model does not unlock a collection,
create an OS session or consent to a provider connection. Operator selection and
the declared-credential interface are independent of provider-owned OAuth state.

Python reads are synchronous: run `reader.read(reference)` off the event loop.
TypeScript uses the explicitly selected, measured Python credential helper and
an asynchronous `read(reference, {signal})`. The helper must carry the matching
SDK and optional native-credential dependencies; see the
[TypeScript author guide](typescript-authoring.md). Both readers recheck the
current alias on every read. Keep values request-scoped and do not cache a
credential across rotation or retirement.

A missing optional alias returns no value. Required absence, an unavailable
selected native entry, damaged selection or an unavailable backend refuses the
read with a sanitized error. None of those cases creates a protected-file store
or selects another native backend. Removing an alias stops new reads; retained
immutable versions may still be needed for previously admitted cleanup. Physical
purging belongs to the owner after its outstanding-obligation audit.

Secret wrappers hide incidental representations. Explicit byte exports remain
private data and require the application's own retention policy. Credentials
must not enter descriptions, proposals, ordinary settings, logs or process
arguments.

## Repeatable native-session qualification

`tests_os_credentials` is a separate, explicit acceptance suite. It runs genuine
isolated public Python and Node applications against a real native backend,
using only temporary synthetic entries in a newly reserved namespace. The test
helper bounds native mutations to eight seconds, reaps its process, and verifies
that all owned entries have been deleted before completing.

Each language tests optional and required absence, rotation, retirement,
undeclared-reference refusal, restart, loss of a selected native entry, missing
selection and explicit repair. Native-entry or selection loss must never create
a file backend. Responses contain comparison results, not credential values.

On a macOS test session, explicitly select `SKULK_TEST_NATIVE_BACKEND=macos_keychain`.
For Linux, select `linux_secret_service` with an unlocked Secret Service on the
current user's normal `/run/user/<uid>/bus`. The dedicated workflow starts a
disposable Secret Service on its fresh Linux runner and refuses to replace an
existing service. It reuses an existing user-owned bus or starts an owned bus
when absent, and closes only its own service processes afterward. It does not
lock or change an operator's existing collection. Headless/file-backend tests
remain separate.

These tests qualify the public credential readers and actual native-session
behavior. Their synthetic startup peers do not establish signed Core/owner
admission, a normal OS-service installation, provider authorization or the
complete SDK1 release. Those retain their independent acceptance evidence.
