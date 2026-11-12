//! Shared private capability implementation, independent of Python and Node.
//!
//! Language bindings must delegate identity-bearing encoding here. The legacy
//! profile is deliberately Python-compatible, including numeric kinds and
//! surrogate code points; normal JSON serializers are not substitutes for it.

#[cfg(unix)]
pub mod calls;
pub mod codec;
pub mod composition;
pub mod contracts;
pub mod handshake;
#[cfg(unix)]
pub mod journal;
pub mod managed;
pub mod portable;
mod portable_json;
mod portable_schema;
pub mod schema;
mod schema_alias;
mod schema_graph;
mod schema_legacy;
mod schema_pattern;
mod schema_reference;
mod schema_scoped;
mod schema_unicode;

#[cfg(unix)]
pub mod binding;

#[cfg(unix)]
pub mod streams;

#[cfg(unix)]
pub mod operations;

#[cfg(unix)]
pub mod operation_logs;

#[cfg(unix)]
pub mod transport;
