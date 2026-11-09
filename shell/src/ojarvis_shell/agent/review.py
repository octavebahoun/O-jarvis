"""Le relecteur du mode shell : un autre modèle relit chaque modification de code proposée.

L'auteur (Muse Spark avec le profil « Tout Meta ») propose un diff ; avant que tu le valides,
le relecteur (Muse Wonder) le lit avec ta demande et cherche les vraies erreurs. Son avis
s'affiche avec le plan, et revient à l'auteur, qui peut corriger. Il ne décide jamais à ta
place : c'est toi qui valides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ojarvis.core.logs import get_logger
from ojarvis.llm.errors import LLMError
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest, Message
from ojarvis.tools.guards import DATA_RULE, as_data

log = get_logger(__name__)

REVIEW_RULES = (
    "Tu relis une modification de code avant que l'utilisateur la valide. Tu reçois sa demande "
    "et le diff (format unifié : + ajouté, - retiré). Cherche seulement les vraies erreurs : "
    "bug, faute de syntaxe, oubli qui empêche de répondre à la demande, faille de sécurité, "
    "secret écrit en clair. Ignore le style et les préférences.\n"
    "Réponds en français, en texte simple : sur la première ligne, OK ou PROBLÈME ; puis une à "
    "trois phrases concrètes (fichier, ligne, correction). PROBLÈME seulement pour une vraie "
    "erreur."
)
#: Ce que le relecteur reçoit au plus (le diff d'un aperçu est déjà coupé à 8 000 caractères).
MAX_REVIEW_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class Review:
    #: True : rien à signaler ; False : un problème ; None : avis illisible.
    ok: bool | None
    notes: str
    provider: str
    model: str

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "notes": self.notes, "provider": self.provider, "model": self.model}

    def for_author(self) -> str:
        """Ce que l'auteur lit, pour corriger si besoin."""
        verdict = {True: "rien à signaler", False: "problème", None: "avis incertain"}[self.ok]
        return f"Relecture ({self.provider} / {self.model}) : {verdict}. {self.notes}".strip()


#: « OK », « **PROBLÈME** : … », « Probleme — … » : le verdict, puis ce qui suit sur la ligne.
VERDICT = re.compile(r"^[\s*#_]*(OK|PROBL[ÈE]ME)\b[\s*#_:.—–-]*(.*)$", re.IGNORECASE)


def parse_review(text: str) -> tuple[bool | None, str]:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None, ""
    match = VERDICT.match(lines[0])
    if match is None:
        return None, " ".join(lines)[:600]
    ok = match.group(1).upper() == "OK"
    notes = " ".join([match.group(2).strip(), *lines[1:]]).strip()
    return ok, notes[:600]


class CodeReviewer:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def review(self, request: str, diffs: list[str]) -> Review | None:
        """L'avis du relecteur, ou None s'il n'a pas pu répondre (la validation continue)."""
        body = "\n\n".join(diffs)[:MAX_REVIEW_CHARS]
        messages = [
            Message("system", f"{REVIEW_RULES}\n\n{DATA_RULE}"),
            Message(
                "user",
                f"Demande de l'utilisateur : {request[:2000]}\n\nModification proposée :\n"
                + as_data(body, origin="diff"),
            ),
        ]
        try:
            result = await self._router.complete(
                "review", LLMRequest(messages=messages, max_tokens=800)
            )
        except LLMError as exc:
            log.warning("code_review_failed", error=type(exc).__name__)
            return None
        ok, notes = parse_review(result.text)
        return Review(ok, notes, result.provider, result.model)
