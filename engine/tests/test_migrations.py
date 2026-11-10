from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from ojarvis.db.base import Base
from ojarvis.db.migrate import (
    alembic_config,
    current_revision,
    managed_by_alembic,
    upgrade_to_head,
)


def head(db_path: Path) -> str | None:
    return ScriptDirectory.from_config(alembic_config(db_path)).get_current_head()


def test_upgrade_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {
        "settings",
        "secret_meta",
        "events",
        "traces",
        "spans",
        "conversations",
        "messages",
        "memory_items",
        "embeddings",
        "messages_fts",
        "memory_items_fts",
        "alembic_version",
    } <= tables
    assert current_revision(db_path) == head(db_path)


def test_models_and_migrations_agree(tmp_path: Path) -> None:
    """Un modèle modifié sans migration ferait échouer ce test."""
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"include_name": managed_by_alembic})
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == []


def test_upgrade_is_idempotent_and_reversible(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    upgrade_to_head(db_path)
    command.downgrade(alembic_config(db_path), "base")
    assert current_revision(db_path) is None
    upgrade_to_head(db_path)
    assert current_revision(db_path) == head(db_path)


def test_full_text_index_follows_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO conversations (id, title, channel, created_at, updated_at, archived) "
                "VALUES "
                "('c1', 't', 'app', '2026-10-14 10:00:00', '2026-10-14 10:00:00', 0)"
            )
        )
        c.execute(
            text(
                "INSERT INTO messages (conversation_id, role, content, status, created_at, meta) "
                "VALUES ('c1', 'user', 'J''adore le café serré', 'ok', '2026-10-14 10:00:00', '{}')"
            )
        )
        found = c.execute(text("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'cafe'"))
        assert found.fetchall() == [(1,)]  # accents ignorés
        c.execute(text("UPDATE messages SET content = 'Plutôt du thé' WHERE id = 1"))
        assert not c.execute(
            text("SELECT 1 FROM messages_fts WHERE messages_fts MATCH 'cafe'")
        ).fetchall()
        c.execute(text("DELETE FROM messages WHERE id = 1"))
        assert not c.execute(
            text("SELECT 1 FROM messages_fts WHERE messages_fts MATCH 'the'")
        ).fetchall()
    engine.dispose()
