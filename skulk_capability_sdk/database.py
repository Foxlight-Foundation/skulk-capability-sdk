"""Private durable SQLite files with explicit transaction ownership."""

import os
import sqlite3
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from .contracts import Denied
from .storage import HostLock, read_private, secure_directory


class Database:
    """Open one connection per transaction; serialize cross-process reservations."""

    def __init__(
        self,
        path: Path,
        schema: str,
        observed_time: Callable[[], int] | None = None,
    ) -> None:
        self.path = path
        self.observed_time = observed_time
        secure_directory(path.parent)
        try:
            fd = os.open(
                path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            os.close(fd)
        except FileExistsError:
            read_private(path, limit=128 * 1024 * 1024)
        with self.transaction() as connection:
            connection.executescript(schema)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """Commit durable intent or roll back atomically on every exception."""
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        try:
            if self.observed_time is not None:
                connection.create_function("observed_time", 0, self.observed_time)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def ownership(self) -> Generator[None]:
        """Fence one complete reconciliation against concurrent journal owners."""
        deadline = time.monotonic() + 2
        while True:
            try:
                lock = HostLock(self.path.parent)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Denied("operation journal is busy") from None
                time.sleep(0.005)
        try:
            yield
        finally:
            lock.close()
