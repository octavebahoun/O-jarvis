"""souvenirs personnels

Un souvenir « personnel » (santé, argent, adresse…) n'est envoyé au modèle que quand la
question en parle. Ajout de colonne simple, sans mode « batch » : les déclencheurs FTS de
`memory_items` restent en place.

Révision : 0010
Précédente : 0009
Créée le : 2026-11-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_items",
        sa.Column("personnel", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # SQLite sait retirer une colonne sans recréer la table (et sans perdre les déclencheurs).
    op.execute("ALTER TABLE memory_items DROP COLUMN personnel")
