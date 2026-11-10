"""Apprentissage : repérer une demande que tu fais souvent, et proposer de l'automatiser.

Après chaque tour (événement `chat.completed`), ta demande est rangée par son sens. Si des
demandes très proches reviennent au moins trois jours différents en un mois, Jarvis propose
d'en faire une automatisation (à l'heure habituelle si tu la fais à heure fixe) ou un skill.
Il ne crée rien lui-même : l'option choisie part dans une conversation, où l'action passe
par un plan que tu valides.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo

from ojarvis.anticipation.store import Option, Suggestion, SuggestionStore
from ojarvis.conversations.store import ConversationStore, MessageRecord
from ojarvis.core.clock import utc_now
from ojarvis.events.bus import Event
from ojarvis.memory.calibration import calibration

#: Deux demandes sont « la même » au-delà d'un seuil propre au modèle de sens
#: (`ojarvis.memory.calibration`) ET avec les mêmes noms propres : « lance les tests de
#: verbelune » et « … de esp32-amp » se ressemblent beaucoup, sans être la même demande.
_WORD = re.compile(r"[\w'-]+")
WINDOW_DAYS = 30
MIN_DAYS = 3
#: Écart maximal entre les heures des demandes pour parler d'une heure habituelle.
HABIT_MINUTES = 120


def names(text: str) -> frozenset[str]:
    """Les noms propres d'une demande (O-jarvis, GitHub, Cotonou…), hors premier mot."""
    words = _WORD.findall(text)[1:]
    return frozenset(
        w.casefold().strip("-'") for w in words if any(c.isupper() or c.isdigit() for c in w)
    )


def _when(minutes: list[int]) -> str:
    middle = int(statistics.median(minutes)) // 5 * 5
    return f"{middle // 60} h {middle % 60:02d}"


class RepetitionWatcher:
    def __init__(
        self,
        conversations: ConversationStore,
        suggestions: SuggestionStore,
        *,
        embedder_id: str,
        timezone: Callable[[], tzinfo],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._conversations = conversations
        self._suggestions = suggestions
        self._threshold = calibration(embedder_id).same_request
        self._timezone = timezone
        self._clock = clock

    async def on_completed(self, event: Event) -> None:
        """Abonné au bus : une demande faite par une automatisation n'est pas une habitude."""
        payload = event.payload
        if payload.get("channel") == "automation":
            return
        conversation_id, reply_id = payload.get("conversation_id"), payload.get("message_id")
        if not isinstance(conversation_id, str) or not isinstance(reply_id, int):
            return
        messages = await self._conversations.messages(conversation_id)
        asked = [m for m in messages if m.role == "user" and m.id < reply_id]
        if asked and len(asked[-1].content) >= 8:
            await self.check(asked[-1])

    async def check(self, request: MessageRecord) -> Suggestion | None:
        await self._conversations.index_request(request)
        since = self._clock() - timedelta(days=WINDOW_DAYS)
        same_names = names(request.content)
        similar = [
            m
            for m, score in await self._conversations.similar_requests(request, since=since)
            if score >= self._threshold and names(m.content) == same_names
        ]
        group = [request, *similar]
        tz = self._timezone()
        moments = [m.created_at.astimezone(tz) for m in group]
        days = {moment.date() for moment in moments}
        if len(days) < MIN_DAYS:
            return None
        minutes = [moment.hour * 60 + moment.minute for moment in moments]
        habitual = max(minutes) - min(minutes) <= HABIT_MINUTES
        short = request.content if len(request.content) <= 80 else request.content[:79] + "…"
        when = _when(minutes)
        automation = (
            f"Crée une automatisation : chaque jour à {when}, fais ceci : {request.content}"
            if habitual
            else f"Crée une automatisation qui fait ceci chaque jour (propose-moi une heure) : "
            f"{request.content}"
        )
        skill = (
            "Crée-toi un skill pour ceci, si c'est un calcul ou une transformation qui se passe "
            f"d'Internet : {request.content}"
        )
        return await self._suggestions.propose(
            kind="repetition",
            title=f"Tu m'as demandé « {short} » {len(days)} jours différents ce mois-ci.",
            detail=f"Souvent vers {when}." if habitual else None,
            options=[
                Option("automatisation", "En faire une automatisation", automation),
                Option("skill", "En faire un skill", skill),
            ],
            confidence=min(0.45 + 0.1 * min(len(days), 5) + (0.1 if habitual else 0.0), 0.95),
            fingerprint=f"repetition:{min(m.id for m in group)}",
        )
