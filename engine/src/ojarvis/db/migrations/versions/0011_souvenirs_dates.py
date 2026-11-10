"""souvenirs datés

Le jour dont parle un souvenir (gardé par le code) et, pour un souvenir de quelques jours, le
moment où il s'efface. Ajouts de colonnes simples, sans mode « batch » : les déclencheurs FTS
de `memory_items` restent en place.

Révision : 0011
Précédente : 0010
Créée le : 2026-11-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import ojarvis.db.base

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_items", sa.Column("event_date", sa.Date(), nullable=True))
    op.add_column(
        "memory_items", sa.Column("expires_at", ojarvis.db.base.UtcDateTime(), nullable=True)
    )


def downgrade() -> None:
    # SQLite sait retirer une colonne sans recréer la table (et sans perdre les déclencheurs).
    op.execute("ALTER TABLE memory_items DROP COLUMN expires_at")
    op.execute("ALTER TABLE memory_items DROP COLUMN event_date")
