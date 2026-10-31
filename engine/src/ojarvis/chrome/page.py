"""Un onglet de Chrome tel que l'extension le lit (titre, adresse, texte visible, sélection,
éléments numérotés) : donné au modèle comme une donnée, jamais comme une consigne, et ses
secrets masqués. Il arrive avec une demande du panneau, ou par l'outil onglet_lire."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ojarvis.agent.reports import urls_in
from ojarvis.agent.turn import AgentTurn
from ojarvis.security.redaction import REDACTOR
from ojarvis.tools.guards import as_data

#: Texte de la page gardé au plus : le début suffit pour répondre, le reste coûterait cher.
MAX_PAGE_CHARS = 20_000


class ElementIn(BaseModel):
    """Un élément de l'onglet sur lequel Jarvis peut agir, numéroté par l'extension."""

    model_config = ConfigDict(extra="forbid")

    ref: int = Field(ge=1, le=100_000)
    kind: Literal["lien", "bouton", "champ", "liste", "case"]
    label: str = Field(default="", max_length=200)
    href: str = Field(default="", max_length=2000)


class PageIn(BaseModel):
    """L'onglet lu par l'extension (jamais ses cookies ni le contenu de ses champs)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    title: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=100_000)
    selection: str = Field(default="", max_length=8000)
    elements: list[ElementIn] = Field(default_factory=list, max_length=300)
    #: L'identifiant de l'onglet dans Chrome : les outils d'onglet le reprennent.
    tab_id: int | None = Field(default=None, ge=0)

    def page(self) -> BrowserPage:
        elements = tuple(PageElement(**e.model_dump()) for e in self.elements)
        return BrowserPage(
            self.url, self.title, self.text, self.selection, elements, tab_id=self.tab_id
        )


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
    tab_id: int | None = None

    def body(self) -> str:
        """Le contenu de l'onglet, secrets masqués (à encadrer comme une donnée)."""
        parts = [f"Titre : {self.title}", f"Adresse : {self.url}"]
        if self.tab_id is not None:
            parts.append(f"Onglet n° {self.tab_id} (à donner aux outils d'onglet)")
        if self.selection.strip():
            parts.append(f"Texte sélectionné :\n{self.selection.strip()}")
        parts.append(f"Texte de la page :\n{self.text[:MAX_PAGE_CHARS]}")
        if self.elements:
            lines = "\n".join(e.line() for e in self.elements)
            parts.append(
                f"Éléments où agir (onglet_cliquer, onglet_remplir : numéro et texte) :\n{lines}"
            )
        return REDACTOR.redact("\n\n".join(parts))

    def block(self) -> str:
        """L'onglet ouvert à côté du panneau, encadré comme une donnée."""
        return "L'onglet ouvert dans Chrome, à côté du panneau :\n" + as_data(
            self.body(), origin=self.url
        )

    def mark(self, turn: AgentTurn) -> None:
        """Lire une page, c'est lire l'extérieur : un plan proposé ensuite est signalé, et
        seules les adresses de la page ou de ta demande sont connues."""
        turn.tainted = True
        turn.web = True
        links = urls_in([e.href for e in self.elements])
        turn.known_urls |= urls_in(self.url) | urls_in(self.text[:MAX_PAGE_CHARS]) | links
