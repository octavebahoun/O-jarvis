"""Tables du moteur. Toute modification passe par une migration Alembic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from ojarvis.db.base import Base


class Setting(Base):
    """Préférence non secrète (les secrets vont dans le trousseau)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(Text)  # JSON sérialisé
    updated_at: Mapped[datetime]


class SecretMeta(Base):
    """Ce qu'on sait d'un secret sans jamais stocker sa valeur."""

    __tablename__ = "secret_meta"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # env | ui | generated
    updated_at: Mapped[datetime]
    last_check_ok: Mapped[bool | None]
    last_check_at: Mapped[datetime | None]
    last_check_detail: Mapped[str | None] = mapped_column(String(300))
    last_check_latency_ms: Mapped[int | None]


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_type_id", "type", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime]
    type: Mapped[str] = mapped_column(String(96))
    source: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]]


class TraceRecord(Base):
    """Une requête suivie de bout en bout (tour de chat, tour vocal, test de clé...)."""

    __tablename__ = "traces"
    __table_args__ = (Index("ix_traces_started_at", "started_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))  # running | ok | error
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    duration_ms: Mapped[float | None]
    meta: Mapped[dict[str, Any]]
    error: Mapped[str | None] = mapped_column(String(500))


class SpanRecord(Base):
    """Une étape d'une trace : appel LLM, outil, transcription, synthèse vocale..."""

    __tablename__ = "spans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    duration_ms: Mapped[float | None]
    attributes: Mapped[dict[str, Any]]
    error: Mapped[str | None] = mapped_column(String(500))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_updated_at", "updated_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(16))  # app | voice | telegram
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    archived: Mapped[bool] = mapped_column(default=False)


class ChatMessage(Base):
    """Un message d'une conversation. Clé entière : stable pour la recherche plein texte."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | interrupted
    created_at: Mapped[datetime]
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(120))
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    cost_usd: Mapped[float | None]
    latency_ms: Mapped[float | None]
    trace_id: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]]


class MemoryItem(Base):
    """Ce que Jarvis sait de toi : fait, préférence ou trait de profil. Visible et modifiable."""

    __tablename__ = "memory_items"
    __table_args__ = (Index("ix_memory_items_kind", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # fait | preference | profil
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # conversation | manuel
    source_message_id: Mapped[int | None]
    confidence: Mapped[float]
    pinned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    use_count: Mapped[int] = mapped_column(default=0)
    #: Santé, argent, adresse… : envoyé au modèle seulement quand la question en parle.
    personnel: Mapped[bool] = mapped_column(default=False, server_default=false())


class Embedding(Base):
    """Vecteur d'un souvenir ou d'un message. Source de vérité ; l'index vec0 en dérive."""

    __tablename__ = "embeddings"

    owner_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # memory | message
    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    dim: Mapped[int]
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime]


class AuditEntry(Base):
    """Journal des actions : ajout seul, chaîné par SHA-256 (des déclencheurs refusent toute
    modification ou suppression). Vérifié à chaque démarrage."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime]
    actor: Mapped[str] = mapped_column(String(32))  # utilisateur | jarvis | automatisation
    action: Mapped[str] = mapped_column(String(64))
    level: Mapped[int]
    summary: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class Plan(Base):
    """Un plan proposé par Jarvis : rien de niveau 2 ou 3 ne s'exécute avant sa validation."""

    __tablename__ = "plans"
    __table_args__ = (Index("ix_plans_status", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))
    request: Mapped[str] = mapped_column(Text)  # la demande de l'utilisateur, pour la synthèse
    status: Mapped[str] = mapped_column(String(16))
    max_level: Mapped[int]
    #: Proposé après lecture d'un contenu externe (page, fichier) : vigilance à la validation.
    tainted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    result: Mapped[str | None] = mapped_column(Text)


class PlanStep(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (Index("ix_plan_steps_plan_position", "plan_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    position: Mapped[int]
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]]
    why: Mapped[str] = mapped_column(String(500))
    level: Mapped[int]
    description: Mapped[str] = mapped_column(String(500))  # ce qui va se passer, sur quoi
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]


class CookieGrant(Base):
    """Cookies d'un domaine importés dans le navigateur de Jarvis, avec ton accord."""

    __tablename__ = "browser_cookie_grants"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # brave | chrome | chromium
    imported_at: Mapped[datetime]
    count: Mapped[int]
    revoked_at: Mapped[datetime | None]


class Automation(Base):
    """Une automatisation : un déclencheur (horaire ou événement) et une action.

    Elle ne déclenche jamais seule une action de niveau 2 ou 3 : elle la prépare (un plan)
    et te prévient."""

    __tablename__ = "automations"
    __table_args__ = (Index("ix_automations_due", "enabled", "next_run_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[dict[str, Any]]
    action: Mapped[dict[str, Any]]
    enabled: Mapped[bool] = mapped_column(default=True)
    #: Ponctuelle : désactivée après son passage.
    one_shot: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(String(16))  # utilisateur | jarvis
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    next_run_at: Mapped[datetime | None]
    last_run_at: Mapped[datetime | None]
    last_status: Mapped[str | None] = mapped_column(String(16))  # ok | failed | missed
    run_count: Mapped[int] = mapped_column(default=0)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )


class WorkflowRun(Base):
    """Une chaîne de tâches persistée : elle reprend à la dernière étape faite après un
    arrêt (plantage, redémarrage du PC)."""

    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_status", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # automation | briefing
    automation_id: Mapped[str | None] = mapped_column(
        ForeignKey("automations.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))  # pending | running | done | failed
    steps: Mapped[list[dict[str, Any]]]
    #: Index de la prochaine étape à faire.
    position: Mapped[int] = mapped_column(default=0)
    context: Mapped[dict[str, Any]]
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]


class Skill(Base):
    """Une version d'un skill : description, outils, code et tests, avec le rapport des tests.

    Une version testée ne change plus (empreinte) ; une seule version d'un skill est active."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        Index("ix_skills_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(300))
    #: Mode d'emploi et outils (nom, description, schéma des arguments).
    manifest: Mapped[dict[str, Any]]
    code: Mapped[str] = mapped_column(Text)
    tests: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    #: brouillon | pret | echec | actif | desactive | remplace
    status: Mapped[str] = mapped_column(String(16))
    report: Mapped[dict[str, Any] | None]
    created_by: Mapped[str] = mapped_column(String(16))  # jarvis | utilisateur
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime]
    tested_at: Mapped[datetime | None]
    activated_at: Mapped[datetime | None]


class McpServer(Base):
    """Un serveur MCP installé depuis le registre officiel (version fixe, lancé isolé).

    Ses clés vont au trousseau ; ici, seulement leurs noms et les réglages ordinaires."""

    __tablename__ = "mcp_servers"

    #: Identifiant local, celui que voit le modèle (ex. « wikipedia-mcp-server »).
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    registry_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500))
    version: Mapped[str] = mapped_column(String(64))
    #: Comment le lancer : paquet npm, Python, image Docker ou service en ligne.
    option: Mapped[dict[str, Any]]
    #: Réglages ordinaires (nom → valeur) et noms des réglages secrets (au trousseau).
    variables: Mapped[dict[str, Any]]
    secrets: Mapped[dict[str, Any]]
    #: Outils vus à l'installation, avec leur niveau.
    tools: Mapped[list[dict[str, Any]]]
    enabled: Mapped[bool] = mapped_column(default=True)
    installed_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class AgentRunRecord(Base):
    """Une mission confiée à un agent (dev, recherche, coach) : tâche, étapes, compte rendu."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_started", "started_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(16))
    task: Mapped[str] = mapped_column(Text)
    #: en_cours | fini | echec | interrompu
    status: Mapped[str] = mapped_column(String(16))
    #: chat (délégué par Jarvis) | interface (confié par toi)
    origin: Mapped[str] = mapped_column(String(16))
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    #: Outils utilisés, dans l'ordre (nom, description, état).
    steps: Mapped[list[dict[str, Any]]]
    #: Plans proposés pendant la mission (ils attendent ta validation).
    plans: Mapped[dict[str, Any]]
    trace_id: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]


class GraphEntity(Base):
    """Un nœud de la mémoire graphe : une personne, un projet, un événement, un lieu…"""

    __tablename__ = "graph_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: moi | personne | projet | evenement | lieu | organisation | sujet
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    #: Nom normalisé (minuscules, sans accents) : une seule entité par nom.
    name_key: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(16))  # conversation | utilisateur
    mentions: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class GraphRelation(Base):
    """Un lien entre deux entités (« travaille sur », « connaît », « participe à »…)."""

    __tablename__ = "graph_relations"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("graph_entities.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("graph_entities.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(80))
    mentions: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class SuggestionRecord(Base):
    """Une suggestion de Jarvis : tirée de tes demandes répétées ou de tes habitudes,
    avec un score de confiance. Elle ne fait rien seule : tu l'acceptes ou tu l'écartes."""

    __tablename__ = "suggestions"
    __table_args__ = (Index("ix_suggestions_status", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: repetition (apprentissage) | inactivite (anticipation)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str | None] = mapped_column(String(500))
    #: Ce que tu peux en faire : [{id, label, prompt}] ; le prompt part dans une conversation.
    options: Mapped[list[dict[str, Any]]]
    confidence: Mapped[float]
    #: Ce qui l'a fait naître : jamais proposée deux fois pour la même raison.
    fingerprint: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # nouvelle | acceptee | ecartee
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
