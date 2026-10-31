"""L'onglet que l'extension Chrome envoie avec une demande : donné au modèle comme une
donnée, jamais comme une consigne, et ses secrets masqués."""

from __future__ import annotations

from dataclasses import dataclass

from ojarvis.agent.reports import urls_in
from ojarvis.agent.turn import AgentTurn
from ojarvis.security.redaction import REDACTOR
from ojarvis.tools.guards import as_data

#: Texte de la page gardé au plus : le début suffit pour répondre, le reste coûterait cher.
MAX_PAGE_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class PageElement:
    """Un lien, un bouton ou un champ de l'onglet, numéroté par l'extension pour y agir."""

    ref: int
    kind: str  # lien | bouton | champ | liste | case
    label: str
    href: str = ""

    def line(self) -> str:
        target = f" → {self.href}" if self.href else ""
        return f"[{self.ref}] {self.kind} « {self.label} »{target}"


@dataclass(frozen=True, slots=True)
class BrowserPage:
    url: str
    title: str
    text: str
    selection: str = ""
    elements: tuple[PageElement, ...] = ()

    def block(self) -> str:
        """L'onglet pour le modèle : encadré comme une donnée, secrets masqués."""
        parts = [f"Titre : {self.title}", f"Adresse : {self.url}"]
        if self.selection.strip():
            parts.append(f"Texte sélectionné :\n{self.selection.strip()}")
        parts.append(f"Texte de la page :\n{self.text[:MAX_PAGE_CHARS]}")
        if self.elements:
            lines = "\n".join(e.line() for e in self.elements)
            parts.append(
                f"Éléments où agir (onglet_cliquer, onglet_remplir : numéro et texte) :\n{lines}"
            )
        body = REDACTOR.redact("\n\n".join(parts))
        return "L'onglet ouvert dans Chrome, à côté du panneau :\n" + as_data(body, origin=self.url)

    def mark(self, turn: AgentTurn) -> None:
        """Lire une page, c'est lire l'extérieur : un plan proposé ensuite est signalé, et
        seules les adresses de la page ou de ta demande sont connues."""
        turn.tainted = True
        turn.web = True
        turn.chrome = True  # ses éléments sont connus : Jarvis peut y agir, avec ton accord
        links = urls_in([e.href for e in self.elements])
        turn.known_urls |= urls_in(self.url) | urls_in(self.text[:MAX_PAGE_CHARS]) | links
