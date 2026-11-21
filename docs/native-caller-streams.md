# Native caller media candidate

Native **0.7.0rc5** adds a caller role for the existing `skulk.transport/2`
channel. It preserves all thirteen issued wire profile identities. Source and
local qualification are separate from binary publication, high-level application
clients and matched owner/Core installed acceptance.

Python exposes `open_transport_v2_client_stream(startup, media_call_id, mode,
seconds=None)`. The typed Node facade exposes `openTransportV2ClientStream`
with the same arguments and a default `null` budget. Use an admitted managed/2
Startup and the **host-assigned** media ID from `delegate_started`, never the
parent call ID or a locally invented grant. The host must bind and consume the
one-use grant against the actual parent connection, child scope and request.
Opening a socket or parsing these records establishes no execution authority.

The returned channel has four methods:

| Python | Node | Behavior |
| --- | --- | --- |
| `send_input(projection, media)` | `sendInput(projection, media)` | Send admitted caller metadata and separate binary media. |
| `next_output()` | `nextOutput()` | Receive provider metadata/media; `None`/`null` requires the matching terminal and end marker. |
| `finish()` | `finish()` | Require consumed media end and close native resources. |
| `close()` | `close()` | Fence further work and await reader/timer/socket cleanup. |

Always await `close` in `finally`. Cancelling an in-progress read/write poisons
the channel; it cannot be reopened or replayed. Concurrent close calls serialize
cleanup, and interrupted cleanup retains task ownership for the next close.
There is no per-stream retry or completed-call history.

`server_streaming` has no caller input. `client_streaming` and `bidirectional`
send caller `started` at sequence zero, then contiguous chunks and a content-free
`completed` half-close. Input completion leaves provider output open. Provider
sequence starts at one because the host owns the started notification.
Client-streaming output has one terminal; server-streaming and bidirectional
output may have chunks before that terminal. Wrong identities, directions,
sequences, modes or terminal/end pairs permanently fence the channel.

Media EOF alone does not mean success. A composed streaming call must also
receive the exact `delegate_finished` control result and complete handler cleanup.
A terminal without end, disconnected channel or early client abandonment cannot
free the parent for another effect. The high-level client and enforcing host
must agree on these conditions before recycling admission.

Metadata and media retain the existing per-frame bounds and eight-packet queue.
Binary media stays separate from JSON; the existing portable scalar/number rules
remain unchanged. A null budget adds no generation timeout. Connect/hello and
partial-frame I/O remain bounded, while finite caller/parent expiry must be
clamped by the composed-call client and independently enforced by the host.
