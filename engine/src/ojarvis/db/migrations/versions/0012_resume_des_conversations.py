"""résumé des conversations

Le résumé d'une conversation et le dernier message qu'il couvre : refait au changement de
sujet ou quand la conversation s'allonge, envoyé au modèle quand les anciens messages ne
tiennent plus dans le contexte.

Révision : 0012
Précédente : 0011
Créée le : 2026-11-10 22:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("summary_upto", sa.Integer(), nullable=True))


def downgrade() -> None:
    # SQLite sait retirer une colonne sans recréer la table.
    op.execute("ALTER TABLE conversations DROP COLUMN summary_upto")
    op.execute("ALTER TABLE conversations DROP COLUMN summary")
