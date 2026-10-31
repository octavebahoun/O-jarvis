import { Check, ChevronDown, Puzzle, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, LoadingState, OfflineState } from "../../components/States";
import { describeError } from "../../lib/engine/client";
import {
  useActivateSkill,
  useDeleteSkill,
  useDisableSkill,
  useSkill,
  useSkills,
  useTestSkill,
} from "../../lib/engine/queries";
import { useEngineOnline } from "../../lib/engine/store";
import type { Skill, SkillStatus } from "../../lib/engine/types";
import { formatMoment } from "../../lib/format";
import { McpSection } from "./McpSection";
import styles from "./Skills.module.css";

const STATUS: Record<SkillStatus, string> = {
  brouillon: "En test",
  pret: "Prêt à activer",
  echec: "Tests en échec",
  actif: "Actif",
  desactive: "Désactivé",
  remplace: "Remplacé par une version plus récente",
};

function Code({ skill }: { skill: Skill }) {
  const detail = useSkill(skill.id, true);
  if (detail.isPending) return <p className={styles.meta}>Lecture du code…</p>;
  if (detail.isError) return <p className={styles.error}>{describeError(detail.error)}</p>;
  return (
    <div className={styles.sources}>
      <h4 className={styles.subTitle}>Code</h4>
      <pre className={styles.code} aria-label={`Code de ${skill.name}`}>
        {detail.data.code}
      </pre>
      <h4 className={styles.subTitle}>Tests</h4>
      <pre className={styles.code} aria-label={`Tests de ${skill.name}`}>
        {detail.data.tests}
      </pre>
      <p className={styles.meta}>Empreinte : {detail.data.digest.slice(0, 16)}…</p>
    </div>
  );
}

function Tests({ skill }: { skill: Skill }) {
  const report = skill.report;
  if (!report) return <p className={styles.meta}>Tests en cours dans le bac à sable…</p>;
  return (
    <div>
      <p className={styles.tests} data-ok={report.ok}>
        Tests : {report.summary}
        {report.duration_ms > 0 && ` · ${(report.duration_ms / 1000).toFixed(1)} s`}
      </p>
      {report.cases.length > 0 && (
        <ul className={styles.cases} aria-label={`Tests de ${skill.name}`}>
          {report.cases.map((c) => (
            <li key={c.name} className={styles.case} data-ok={c.ok}>
              {c.ok ? (
                <Check className={styles.caseIcon} aria-label="réussi" />
              ) : (
                <X className={styles.caseIcon} aria-label="échoué" />
              )}
              <code>{c.name}</code>
              {c.detail && <span className={styles.caseDetail}>{c.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SkillCard({ skill, online }: { skill: Skill; online: boolean }) {
  const [showCode, setShowCode] = useState(false);
  const test = useTestSkill();
  const activate = useActivateSkill();
  const disable = useDisableSkill();
  const remove = useDeleteSkill();
  const titleId = useId();
  const codeId = useId();
  const error = test.error ?? activate.error ?? disable.error ?? remove.error;
  const tools = skill.tools.map((t) => t.tool).join(", ");
  const canActivate = (skill.status === "pret" || skill.status === "desactive") && skill.report?.ok;
  return (
    <article className={styles.card} data-status={skill.status} aria-labelledby={titleId}>
      <header className={styles.cardHead}>
        <Puzzle className={styles.cardIcon} aria-hidden="true" />
        <h3 id={titleId} className={styles.cardTitle}>
          {skill.name} <span className={styles.version}>v{skill.version}</span>
        </h3>
        <span className={styles.status} data-status={skill.status}>
          {STATUS[skill.status]}
        </span>
      </header>
      <p className={styles.description}>{skill.description}</p>
      {skill.instructions && (
        <p className={styles.meta}>Mode d&apos;emploi : {skill.instructions}</p>
      )}
      <ul className={styles.tools} aria-label={`Outils de ${skill.name}`}>
        {skill.tools.map((t) => (
          <li key={t.name}>
            <code className={styles.toolName}>{t.tool}</code> — {t.description}
          </li>
        ))}
      </ul>
      <p className={styles.permissions}>Permissions : {skill.permissions}.</p>
      <Tests skill={skill} />
      <p className={styles.meta}>
        {skill.created_by === "jarvis" ? "Écrit par Jarvis" : "Ajouté par toi"} le{" "}
        {formatMoment(skill.created_at)}
        {skill.activated_at && ` · activé le ${formatMoment(skill.activated_at)}`}
      </p>
      {error && (
        <p className={styles.error} role="alert">
          {describeError(error)}
        </p>
      )}
      <div className={styles.cardActions}>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={showCode}
          aria-controls={codeId}
          onClick={() => {
            setShowCode((v) => !v);
          }}
        >
          Voir le code
          <ChevronDown className={styles.chevron} data-open={showCode} aria-hidden="true" />
        </Button>
        {skill.status !== "actif" && skill.status !== "remplace" && (
          <Button
            size="sm"
            disabled={!online}
            busy={test.isPending}
            onClick={() => {
              test.mutate(skill.id);
            }}
          >
            Relancer les tests
          </Button>
        )}
        {canActivate && (
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="primary" disabled={!online} busy={activate.isPending}>
                Activer…
              </Button>
            }
            title={`Activer «\u00a0${skill.name}\u00a0» v${skill.version} ?`}
            description={`Jarvis pourra utiliser ${skill.tools.length > 1 ? "ses outils" : "son outil"} (${tools}) sans te demander : ${skill.permissions}. Tests : ${skill.report?.summary ?? "?"}. Tu peux le désactiver à tout moment.`}
            confirmLabel="Activer"
            onConfirm={() => {
              activate.mutate(skill.id);
            }}
          />
        )}
        {skill.status === "actif" && (
          <Button
            size="sm"
            disabled={!online}
            busy={disable.isPending}
            onClick={() => {
              disable.mutate(skill.id);
            }}
          >
            Désactiver
          </Button>
        )}
        {skill.status !== "actif" && (
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="ghost" disabled={!online}>
                Supprimer
              </Button>
            }
            title={`Supprimer «\u00a0${skill.name}\u00a0» v${skill.version} ?`}
            description="Cette version, son code et ses tests seront effacés. Les autres versions restent."
            confirmLabel="Supprimer"
            cancelLabel="Garder"
            onConfirm={() => {
              remove.mutate(skill.id);
            }}
          />
        )}
      </div>
      {showCode && (
        <div id={codeId}>
          <Code skill={skill} />
        </div>
      )}
    </article>
  );
}

/** Par nom, puis de la version la plus récente à la plus ancienne. */
function ordered(skills: Skill[]): Skill[] {
  const version = (s: Skill) => s.version.split(".").map((n) => Number(n));
  return [...skills].sort((a, b) => {
    if (a.name !== b.name) return a.name.localeCompare(b.name);
    const [va, vb] = [version(a), version(b)];
    for (let i = 0; i < 3; i += 1) if (va[i] !== vb[i]) return (vb[i] ?? 0) - (va[i] ?? 0);
    return 0;
  });
}

export function SkillsSection() {
  const skills = useSkills();
  const online = useEngineOnline();
  if (!online && !skills.data) return <OfflineState what="La liste des skills" />;
  if (skills.isPending) return <LoadingState label="Lecture des skills…" />;
  if (skills.isError)
    return (
      <ErrorState
        message={describeError(skills.error)}
        onRetry={() => {
          void skills.refetch();
        }}
      />
    );
  if (skills.data.length === 0)
    return (
      <EmptyState title="Aucun skill pour l'instant">
        Demande par exemple «&nbsp;Crée-toi un skill qui compte les jours entre deux dates&nbsp;» :
        Jarvis l&apos;écrit, le teste dans le bac à sable, puis te demande de l&apos;activer.
      </EmptyState>
    );
  return (
    <ul className={styles.list} aria-label="Skills">
      {ordered(skills.data).map((skill) => (
        <li key={skill.id}>
          <SkillCard skill={skill} online={online} />
        </li>
      ))}
    </ul>
  );
}

export function SkillsScreen() {
  return (
    <div className={styles.page}>
      <PageHeader
        title="Skills et plugins"
        description="Ce qui étend Jarvis : les skills qu'il écrit (code testé dans le bac à sable) et les serveurs MCP du registre. Rien ne s'active sans ta double confirmation."
      />
      <section className={styles.section} aria-labelledby="skills-titre">
        <h2 id="skills-titre" className={styles.sectionTitle}>
          Skills
        </h2>
        <SkillsSection />
      </section>
      <section className={styles.section} aria-labelledby="mcp-titre">
        <h2 id="mcp-titre" className={styles.sectionTitle}>
          Serveurs MCP
        </h2>
        <p className={styles.meta}>
          Des outils publiés par d&apos;autres, trouvés dans le registre officiel. Chacun est testé
          de façon isolée avant d&apos;être installé, et chaque action qu&apos;il propose attend ta
          validation.
        </p>
        <McpSection />
      </section>
    </div>
  );
}
