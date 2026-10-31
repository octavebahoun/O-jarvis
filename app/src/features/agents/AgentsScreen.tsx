import { Bot, Check, CircleDot, LoaderCircle, Send, X } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { Link } from "react-router";

import { Button } from "../../components/Button";
import { PageHeader } from "../../components/PageHeader";
import { RichText } from "../../components/RichText";
import { EmptyState, ErrorState, LoadingState, OfflineState } from "../../components/States";
import { describeError } from "../../lib/engine/client";
import { useAgentRuns, useAgents, useStartMission } from "../../lib/engine/queries";
import { useEngineOnline } from "../../lib/engine/store";
import type { AgentInfo, AgentRun, AgentStep } from "../../lib/engine/types";
import { formatMoment } from "../../lib/format";
import chat from "../chat/Chat.module.css";
import styles from "./Agents.module.css";

const RUN_STATUS: Record<AgentRun["status"], string> = {
  en_cours: "En mission",
  fini: "Terminée",
  echec: "En échec",
  interrompu: "Interrompue (Jarvis s'est arrêté)",
  suspendue: "Suspendue (garde-fou atteint)",
};

function StepIcon({ step }: { step: AgentStep }) {
  if (step.status === "failed") return <X className={styles.stepIcon} aria-label="échouée" />;
  if (step.status === "proposed")
    return <CircleDot className={styles.stepIcon} aria-label="plan proposé" />;
  if (step.status === "running")
    return <LoaderCircle className={styles.stepIcon} aria-label="en cours" />;
  return <Check className={styles.stepIcon} aria-label="faite" />;
}

function Steps({ run }: { run: AgentRun }) {
  if (run.steps.length === 0)
    return (
      <p className={styles.meta}>
        {run.status === "en_cours" ? "Il réfléchit…" : "Aucun outil utilisé."}
      </p>
    );
  return (
    <ol className={styles.steps} aria-label="Étapes">
      {run.steps.map((step, index) => (
        <li key={`${index}-${step.tool}`} className={styles.step} data-status={step.status}>
          <StepIcon step={step} />
          <span>{step.description}</span>
          {step.error && <span className={styles.stepError}>{step.error}</span>}
        </li>
      ))}
    </ol>
  );
}

function MissionForm({ agent, online }: { agent: AgentInfo; online: boolean }) {
  const [task, setTask] = useState("");
  const start = useStartMission();
  const fieldId = useId();
  const submit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (task.trim().length < 5) return;
    start.mutate(
      { agent: agent.name, task: task.trim() },
      {
        onSuccess: () => {
          setTask("");
        },
      },
    );
  };
  return (
    <form className={styles.form} onSubmit={submit}>
      <label htmlFor={fieldId} className={styles.formLabel}>
        Lui confier une tâche
      </label>
      <textarea
        id={fieldId}
        className={styles.textarea}
        rows={2}
        value={task}
        placeholder="Une tâche complète : il ne voit pas tes conversations."
        onChange={(event) => {
          setTask(event.target.value);
        }}
      />
      {start.isError && (
        <p className={styles.error} role="alert">
          {describeError(start.error)}
        </p>
      )}
      <Button
        type="submit"
        size="sm"
        icon={<Send />}
        disabled={!online || agent.busy || task.trim().length < 5}
        busy={start.isPending}
      >
        Confier
      </Button>
    </form>
  );
}

function AgentCard({
  agent,
  current,
  online,
}: {
  agent: AgentInfo;
  current: AgentRun | undefined;
  online: boolean;
}) {
  const titleId = useId();
  return (
    <article className={styles.agent} data-busy={agent.busy} aria-labelledby={titleId}>
      <header className={styles.head}>
        <Bot className={styles.icon} aria-hidden="true" />
        <h2 id={titleId} className={styles.title}>
          {agent.label}
        </h2>
        <span className={styles.status} data-busy={agent.busy}>
          {agent.busy ? "En mission" : "Disponible"}
        </span>
      </header>
      <p className={styles.goal}>{agent.goal.charAt(0).toUpperCase() + agent.goal.slice(1)}</p>
      <ul className={styles.tools} aria-label={`Outils de l'agent ${agent.label}`}>
        {agent.tools.map((tool) => (
          <li key={tool}>
            <code>{tool}</code>
          </li>
        ))}
      </ul>
      {agent.busy && current && (
        <div className={styles.live} aria-live="polite">
          <p className={styles.liveTask}>«&nbsp;{current.task}&nbsp;»</p>
          <Steps run={current} />
        </div>
      )}
      <MissionForm agent={agent} online={online} />
    </article>
  );
}

function RunCard({ run, label }: { run: AgentRun; label: string }) {
  return (
    <article className={styles.run} data-status={run.status}>
      <header className={styles.runHead}>
        <span className={styles.runAgent}>{label}</span>
        <span className={styles.runStatus} data-status={run.status}>
          {RUN_STATUS[run.status]}
        </span>
        <span className={styles.meta}>
          {formatMoment(run.started_at)}
          {run.origin === "chat" ? " · confiée par Jarvis" : " · confiée par toi"}
        </span>
      </header>
      <p className={styles.runTask}>«&nbsp;{run.task}&nbsp;»</p>
      <Steps run={run} />
      {run.result && <RichText text={run.result} className={chat.markdown} />}
      {run.error && <p className={styles.error}>{run.error}</p>}
      {(run.plans.length > 0 || run.trace_id) && (
        <p className={styles.meta}>
          {run.plans.length > 0 && (
            <Link to="/actions">
              {run.plans.length === 1
                ? "Un plan attend ta validation"
                : `${run.plans.length} plans attendent ta validation`}
            </Link>
          )}
          {run.plans.length > 0 && run.trace_id && " · "}
          {run.trace_id && <Link to="/journal">Voir la trace (coût, latence)</Link>}
        </p>
      )}
    </article>
  );
}

export function AgentsScreen() {
  const agents = useAgents();
  const runs = useAgentRuns();
  const online = useEngineOnline();

  let body;
  if (!online && !agents.data) body = <OfflineState what="Les agents" />;
  else if (agents.isPending) body = <LoadingState label="Lecture des agents…" />;
  else if (agents.isError)
    body = (
      <ErrorState
        message={describeError(agents.error)}
        onRetry={() => {
          void agents.refetch();
        }}
      />
    );
  else {
    const labels = Object.fromEntries(agents.data.map((a) => [a.name, a.label]));
    body = (
      <>
        <ul className={styles.agents} aria-label="Agents">
          {agents.data.map((agent) => (
            <li key={agent.name}>
              <AgentCard
                agent={agent}
                current={runs.data?.find((r) => r.id === agent.run)}
                online={online}
              />
            </li>
          ))}
        </ul>
        <section className={styles.history} aria-labelledby="missions-titre">
          <h2 id="missions-titre" className={styles.sectionTitle}>
            Missions récentes
          </h2>
          {runs.isPending ? (
            <LoadingState label="Lecture des missions…" />
          ) : runs.isError ? (
            <ErrorState message={describeError(runs.error)} />
          ) : runs.data.length === 0 ? (
            <EmptyState title="Aucune mission pour l'instant">
              Confie une tâche à un agent ci-dessus, ou demande à Jarvis «&nbsp;fais faire une
              recherche sur…&nbsp;» : il la déléguera.
            </EmptyState>
          ) : (
            <ul className={styles.runs} aria-label="Missions récentes">
              {runs.data.map((run) => (
                <li key={run.id}>
                  <RunCard run={run} label={labels[run.agent] ?? run.agent} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Agents"
        description="Des assistants spécialisés à qui Jarvis, ou toi, confies une tâche. Chacun a son objectif et ses outils ; il lit seul, et toute action attend ta validation."
      />
      {body}
    </div>
  );
}
