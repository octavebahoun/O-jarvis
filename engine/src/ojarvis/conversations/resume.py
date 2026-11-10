"""« Résumer la conversation ? » : le résumé des anciens messages, pour quand ils ne tiennent
plus dans le contexte.

Deux moments le refont, après la réponse :
- le Système 1 voit un changement de sujet, un récapitulatif demandé ou une fin de séance :
  tout ce qui précède est résumé (un sujet clos se résume bien d'un bloc) ;
- la longueur, c'est le code : au-delà d'environ 4 000 jetons non résumés, tout sauf les six
  derniers messages.

Le résumé reprend le précédent : il couvre la conversation depuis son début. Le contexte
l'envoie au modèle en plus des messages récents ; les anciens messages y restent tant que le
budget le permet.
"""

from __future__ import annotations

from collections.abc import Mapping

from ojarvis.context.engine import estimate_tokens
from ojarvis.conversations.store import ConversationStore, MessageRecord
from ojarvis.core.logs import get_logger
from ojarvis.llm.errors import LLMError
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest, Message
from ojarvis.systeme1.decideurs import Choix

log = get_logger(__name__)

#: Mesuré le 10/11/2026 sur les phrases de test : aucun « oui » faux à partir de 0,6.
SEUIL_RESUMER = 0.6
#: Moins que trois échanges : rien à résumer.
AU_MOINS = 6
#: Au-delà, la conversation est résumée même sans changement de sujet…
LONGUEUR = 4000
#: … sauf ses derniers messages, gardés mot pour mot.
GARDES = 6

PROMPT = (
    "Tu résumes une conversation entre l'utilisateur et Jarvis, pour que Jarvis s'en souvienne "
    "quand les anciens messages ne tiennent plus dans son contexte. En français, dix puces au "
    "plus : les sujets abordés, ce qui a été décidé ou produit (noms de fichiers, commandes, "
    "chiffres exacts), ce qui reste à faire. Reprends le résumé précédent s'il y en a un. "
    "N'invente rien, ne donne pas de conseil."
)


def resume_voulu(decisions: Mapping[str, Choix] | None) -> bool:
    choix = decisions.get("resumer") if decisions else None
    return choix is not None and choix.reponse == "oui" and choix.confiance >= SEUIL_RESUMER


def a_resumer(
    messages: list[MessageRecord], depuis: int, avant: int, *, sujet_change: bool
) -> list[MessageRecord]:
    """Les messages que le nouveau résumé doit couvrir ; vide s'il n'y a pas lieu."""
    anciens = [
        m for m in messages if depuis < m.id < avant and m.status == "ok" and m.content.strip()
    ]
    if sujet_change and len(anciens) >= AU_MOINS:
        return anciens
    if sum(estimate_tokens(m.content) for m in anciens) > LONGUEUR:
        return anciens[:-GARDES]
    return []


async def resumer(
    conversations: ConversationStore,
    router: ModelRouter,
    conversation_id: str,
    avant: int,
    *,
    sujet_change: bool,
) -> bool:
    """Refait le résumé des messages d'avant `avant` s'il y a lieu ; vrai s'il a été refait."""
    precedent = await conversations.summary(conversation_id)
    texte, depuis = precedent if precedent else ("", 0)
    couverts = a_resumer(
        await conversations.messages(conversation_id, limit=400),
        depuis,
        avant,
        sujet_change=sujet_change,
    )
    if not couverts:
        return False
    echanges = "\n".join(
        f"{'Utilisateur' if m.role == 'user' else 'Jarvis'} : {m.content[:1500]}" for m in couverts
    )
    contenu = (
        f"Résumé précédent :\n{texte or '(aucun)'}\n\nSuite de la conversation :\n{echanges}"
    )[-14000:]
    try:
        result = await router.complete(
            "extract",
            LLMRequest(
                messages=[Message("system", PROMPT), Message("user", contenu)], max_tokens=600
            ),
        )
    except LLMError:
        log.warning("conversation_summary_failed")
        return False
    if not result.text.strip():
        return False
    await conversations.set_summary(conversation_id, result.text.strip(), couverts[-1].id)
    log.info("conversation_summarized", messages=len(couverts), sujet_change=sujet_change)
    return True
