"""Index vectoriel : vecteurs stockés en table (source de vérité), recherche par sqlite-vec.

Si l'extension sqlite-vec n'est pas disponible, la recherche se fait en NumPy sur les
mêmes vecteurs : plus lent sur de gros volumes, mais exact et sans dépendance.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ojarvis.core.clock import utc_now
from ojarvis.db.engine import Database
from ojarvis.db.models import Embedding
from ojarvis.memory.embeddings import Vectors

_SAFE = re.compile(r"^[a-z0-9_]+$")


class VectorIndex:
    def __init__(self, db: Database, owner_type: str, model_id: str, dim: int) -> None:
        if not _SAFE.match(owner_type) or not _SAFE.match(model_id):
            raise ValueError("nom d'index invalide")
        self._db = db
        self.owner_type = owner_type
        self.model_id = model_id
        self.dim = dim
        self.table = f"vec_{owner_type}_{model_id}"
        self.native = False

    async def ensure(self) -> None:
        """Crée l'index sqlite-vec si possible, et le resynchronise depuis la table."""
        self.native = await self._db.run(self._has_vec)
        if not self.native:
            return

        def create_and_sync(session: Session) -> None:
            session.connection().exec_driver_sql(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.table} USING "
                f"vec0(embedding float[{self.dim}] distance_metric=cosine)"
            )
            indexed = (
                session.connection()
                .exec_driver_sql(
                    f"SELECT count(*) FROM {self.table}"  # noqa: S608
                )
                .scalar_one()
            )
            stored = session.scalars(
                select(Embedding).where(
                    Embedding.owner_type == self.owner_type, Embedding.model == self.model_id
                )
            ).all()
            if indexed == len(stored):
                return
            session.connection().exec_driver_sql(f"DELETE FROM {self.table}")  # noqa: S608
            for row in stored:
                session.connection().exec_driver_sql(
                    f"INSERT INTO {self.table}(rowid, embedding) VALUES (?, ?)",  # noqa: S608
                    (row.owner_id, row.vector),
                )

        await self._db.run(create_and_sync)

    @staticmethod
    def _has_vec(session: Session) -> bool:
        try:
            session.connection().exec_driver_sql("SELECT vec_version()")
        except Exception:
            return False
        return True

    async def upsert(self, owner_id: int, vector: Vectors) -> None:
        blob = np.asarray(vector, dtype=np.float32).reshape(-1).tobytes()

        def write(session: Session) -> None:
            session.merge(
                Embedding(
                    owner_type=self.owner_type,
                    owner_id=owner_id,
                    model=self.model_id,
                    dim=self.dim,
                    vector=blob,
                    created_at=utc_now(),
                )
            )
            if self.native:
                conn = session.connection()
                conn.exec_driver_sql(
                    f"DELETE FROM {self.table} WHERE rowid = ?",  # noqa: S608
                    (owner_id,),
                )
                conn.exec_driver_sql(
                    f"INSERT INTO {self.table}(rowid, embedding) VALUES (?, ?)",  # noqa: S608
                    (owner_id, blob),
                )

        await self._db.run(write)

    async def delete(self, owner_ids: Iterable[int]) -> None:
        ids = list(owner_ids)
        if not ids:
            return

        def remove(session: Session) -> None:
            session.execute(
                delete(Embedding).where(
                    Embedding.owner_type == self.owner_type,
                    Embedding.model == self.model_id,
                    Embedding.owner_id.in_(ids),
                )
            )
            if self.native:
                for owner_id in ids:
                    session.connection().exec_driver_sql(
                        f"DELETE FROM {self.table} WHERE rowid = ?",  # noqa: S608
                        (owner_id,),
                    )

        await self._db.run(remove)

    async def search(
        self, vector: Vectors, k: int, *, exclude: set[int] | None = None
    ) -> list[tuple[int, float]]:
        """Les `k` plus proches, avec leur similarité cosinus (1 = identique)."""
        exclude = exclude or set()
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        fetch = k + len(exclude)

        def native(session: Session) -> list[tuple[int, float]]:
            rows = (
                session.connection()
                .exec_driver_sql(
                    f"SELECT rowid, distance FROM {self.table} "  # noqa: S608
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (query.tobytes(), fetch),
                )
                .all()
            )
            # Un vecteur nul (un texte sans mot, comme « ok ») n'a pas de distance cosinus :
            # sqlite-vec la rend vide, et ce voisin ne ressemble à rien.
            return [(int(r[0]), 1.0 - float(r[1])) for r in rows if r[1] is not None]

        def brute(session: Session) -> list[tuple[int, float]]:
            rows = session.execute(
                select(Embedding.owner_id, Embedding.vector).where(
                    Embedding.owner_type == self.owner_type, Embedding.model == self.model_id
                )
            ).all()
            if not rows:
                return []
            flat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
            matrix = flat.reshape(len(rows), self.dim)
            scores = matrix @ query
            order = np.argsort(-scores)[:fetch]
            return [(int(rows[i][0]), float(scores[i])) for i in order]

        found = await self._db.run(native if self.native else brute)
        return [(owner, score) for owner, score in found if owner not in exclude][:k]
