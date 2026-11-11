import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeEngine, renderScreen } from "../../test/render";
import { ChatMessage } from "./ChatMessage";
import { type ChatItem, withLive } from "./useChat";

function reply(over: Partial<ChatItem> = {}): ChatItem {
  return {
    key: "a1",
    id: 2,
    role: "assistant",
    content: "Voici un premier plan d'architecture.",
    status: "ok",
    meta: {
      provider: "deepseek",
      model: "deepseek-flash",
      latencyMs: 900,
      firstTokenMs: 300,
      costUsd: 0.001,
      traceId: null,
      fallbackFrom: null,
    },
    context: null,
    error: null,
    tools: [],
    plans: [],
    stronger: { model: "deepseek/deepseek-v4-pro", label: "DeepSeek V4 Pro" },
    ...over,
  };
}

describe("Proposer un modèle plus fort", () => {
  it("propose de refaire une tâche lourde avec un modèle plus fort, sans rien changer seul", async () => {
    const { client } = fakeEngine(() => [404, {}]);
    const onStronger = vi.fn();
    renderScreen(<ul>{<ChatMessage item={reply()} onStronger={onStronger} />}</ul>, { client });
    expect(onStronger).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Tâche lourde : refaire avec DeepSeek V4 Pro" }),
    );
    expect(onStronger).toHaveBeenCalledWith("deepseek/deepseek-v4-pro");
  });

  it("ne propose rien quand la réponse vient déjà de ce modèle, ou n'est pas finie", () => {
    const { client } = fakeEngine(() => [404, {}]);
    const base = reply();
    const strong = reply(base.meta ? { meta: { ...base.meta, model: "deepseek-v4-pro" } } : {});
    renderScreen(
      <ul>
        <ChatMessage item={strong} onStronger={vi.fn()} />
        <ChatMessage item={reply({ key: "a2", status: "streaming" })} onStronger={vi.fn()} />
        <ChatMessage item={reply({ key: "a3", stronger: null })} onStronger={vi.fn()} />
      </ul>,
      { client },
    );
    expect(screen.queryByRole("button", { name: /refaire avec/ })).toBeNull();
  });

  it("garde la proposition et les souvenirs utilisés quand la conversation se recharge", () => {
    const { client } = fakeEngine(() => [404, {}]);
    const souvenir = { kind: "fait", preview: "L'utilisateur habite à Lyon.", kept: true };
    const direct = reply({ context: [souvenir] });
    // Réponse finie : la conversation rechargée la renvoie, enregistrée, sans ce que seul le
    // direct connaît.
    const enregistre = reply({ key: "m2", stronger: null, context: null });
    const items = withLive([enregistre], [direct]);
    expect(items).toHaveLength(1);
    expect(items[0]?.context).toEqual([souvenir]);
    renderScreen(
      <ul>
        {items.map((item) => (
          <ChatMessage key={item.key} item={item} onStronger={vi.fn()} />
        ))}
      </ul>,
      { client },
    );
    expect(
      screen.getByRole("button", { name: "Tâche lourde : refaire avec DeepSeek V4 Pro" }),
    ).toBeTruthy();
  });
});
