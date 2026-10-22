import sqlite3
from pathlib import Path


class FileCache:
    """Хранит Telegram file_id уже отправленных файлов, чтобы повторно не качать их."""

    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            " key TEXT PRIMARY KEY, file_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        self._db.commit()

    def get(self, key: str) -> tuple[str, str] | None:
        row = self._db.execute("SELECT file_id, kind FROM files WHERE key = ?", (key,)).fetchone()
        return (row[0], row[1]) if row else None

    def put(self, key: str, file_id: str, kind: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO files (key, file_id, kind) VALUES (?, ?, ?)", (key, file_id, kind)
        )
        self._db.commit()

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM files WHERE key = ?", (key,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
