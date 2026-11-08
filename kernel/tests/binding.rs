#![cfg(unix)]
//! Shared boundary refusals must occur before recovery or storage mutation.
use std::fs;
use std::os::unix::fs::PermissionsExt;

use skulk_capability_kernel::{binding, codec, journal::JournalError};

#[test]
fn tagged_transport_keeps_numeric_and_unicode_identity_and_refuses_ambiguity() {
    let input = r#"["object",[[[110],["float","8000000000000000"]],[[115],["string",[55296]]],[[98],["integer","9007199254740993"]]]]"#;
    assert_eq!(
        codec::encode(&binding::tagged(input).unwrap()).unwrap(),
        br#"{"b":9007199254740993,"n":-0.0,"s":"\ud800"}"#
    );
    for malformed in [
        r#"["integer","01"]"#,
        r#"["float","7ff0000000000000"]"#,
        r#"["string",[1114112]]"#,
        r#"["null",0]"#,
        r#"["object",[[[97],["null"]],[[97],["bool",true]]]]"#,
    ] {
        assert!(binding::tagged(malformed).is_err());
    }
}

#[test]
fn invalid_open_cannot_recover_or_create_a_journal() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    for (bounds, now) in [("{}", -1), (r#"{"retain":0}"#, 1), (r#"{"retian":4}"#, 1)] {
        assert!(matches!(
            binding::open(&path, bounds, now),
            Err(JournalError::Invalid)
        ));
        assert!(!path.exists());
    }
}

#[test]
fn journal_request_typos_and_missing_time_are_refused_without_changes() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let path = root.path().join("operations.sqlite");
    let mut journal = binding::open(&path, "{}", 10).unwrap();
    let digest = codec::input_digest("s", "{}").unwrap();
    journal.reserve("op", &digest, "{}", "s", 10).unwrap();
    let before = journal.status("op", "s", 10).unwrap();
    for request in [
        r#"{"method":"update","operation_id":"op","scope":"s","now":20,"stat":"running"}"#,
        r#"{"method":"update","operation_id":"op","scope":"s","state":"running"}"#,
        r#"{"method":"page","now":20,"operation_id":"op"}"#,
    ] {
        assert!(matches!(
            binding::transact(&mut journal, request),
            Err(JournalError::Invalid)
        ));
        assert_eq!(journal.status("op", "s", 10).unwrap(), before);
    }
}
