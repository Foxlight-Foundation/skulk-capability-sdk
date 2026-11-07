"""The Skulk capability authoring SDK: what a capability is written against.

Eight modules: contracts (the manifest and wire vocabulary), wire (local IPC
framing), storage (owner-only files and locks), operations (durable jobs),
database (the durable SQLite journal), streams (duplex lifecycle and binary
media), credentials (declared, protected secret reads), and serving (where a
capability's own server listens and whom it admits). Everything a capability
needs to speak to its host, and nothing about how the host works.
"""

from .contracts import ACCEPTED_PROTOCOLS as ACCEPTED_PROTOCOLS
from .contracts import PROTOCOL as PROTOCOL
