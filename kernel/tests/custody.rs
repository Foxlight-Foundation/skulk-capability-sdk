#![cfg(unix)]

use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};

use skulk_capability_kernel::journal::{Bounds, Journal, JournalError, Ownership};

#[test]
fn early_fence_precedes_journal_creation_and_preserves_its_inode() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let marker = root.path().join("native-jobs.json");
    fs::write(&marker, b"existing binding and uncertainty metadata").unwrap();
    let held = Ownership::acquire(root.path()).unwrap();
    let lock = root.path().join("supervisor.lock");
    let inode = fs::metadata(&lock).unwrap().ino();
    let journal = root.path().join("operations.sqlite");
    assert!(matches!(
        Journal::open(&journal, Bounds::default(), 100),
        Err(JournalError::Busy)
    ));
    assert!(!journal.exists());
    assert_eq!(
        fs::read(&marker).unwrap(),
        b"existing binding and uncertainty metadata"
    );
    drop(held);
    let next = Ownership::acquire(root.path()).unwrap();
    assert_eq!(fs::metadata(&lock).unwrap().ino(), inode);
    assert_eq!(fs::metadata(&lock).unwrap().len(), 0);
    drop(next);
    Journal::open(&journal, Bounds::default(), 100).unwrap();
}

#[tokio::test]
async fn async_contention_does_not_block_the_control_executor() {
    let root = tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let held = Ownership::acquire(root.path()).unwrap();
    let waiting = tokio::spawn(Ownership::open(root.path().to_owned()));
    tokio::time::timeout(
        std::time::Duration::from_millis(500),
        tokio::time::sleep(std::time::Duration::from_millis(10)),
    )
    .await
    .unwrap();
    assert!(!waiting.is_finished());
    drop(held);
    let acquired = tokio::time::timeout(std::time::Duration::from_secs(3), waiting)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    drop(acquired);
    Ownership::acquire(root.path()).unwrap();
}

#[test]
fn unsafe_directories_and_lock_files_are_refused_without_following_links() {
    let root = tempfile::tempdir().unwrap();
    let target = root.path().join("target");
    fs::create_dir(&target).unwrap();
    fs::set_permissions(&target, fs::Permissions::from_mode(0o700)).unwrap();
    let alias = root.path().join("alias");
    symlink(&target, &alias).unwrap();
    assert!(matches!(
        Ownership::acquire(&alias),
        Err(JournalError::PrivateDirectory)
    ));
    let secret = root.path().join("untouched");
    fs::write(&secret, b"do not open or replace").unwrap();
    let lock = target.join("supervisor.lock");
    symlink(&secret, &lock).unwrap();
    assert!(Ownership::acquire(&target).is_err());
    assert_eq!(fs::read(&secret).unwrap(), b"do not open or replace");
    fs::remove_file(&lock).unwrap();
    fs::write(&lock, b"").unwrap();
    fs::set_permissions(&lock, fs::Permissions::from_mode(0o640)).unwrap();
    assert!(matches!(
        Ownership::acquire(&target),
        Err(JournalError::PrivateFile)
    ));
}
