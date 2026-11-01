"""Le chat de bout en bout, avec des fournisseurs simulés : flux SSE, historique, mémoire."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ojarvis.chat.learning import clean_title
from ojarvis.llm.errors import ProviderUnavailableError
from ojarvis.llm.providers.fake import FakeProvider, FakeReply, e2e_script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, Message, ToolCall, ToolSpec
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse


def smart_script(request: LLMRequest) -> FakeReply:
    """Répond au chat, titre les conversations et extrait un fait sur le café."""
    system = request.messages[0].content
    if request.tools and request.tools[0].name == TOOL_NAME:
        if "café" in request.messages[-2].content or "café" in system:
            return FakeReply(
                tool_calls=[
                    ToolCall(
                        "1",
                        TOOL_NAME,
                        {
                            "faits": [
                                {
                                    "type": "preference",
                                    "contenu": "Octave boit du café chaque matin.",
                                    "confiance": 0.9,
                                }
                            ]
                        },
                    )
                ]
            )
        return FakeReply(tool_calls=[ToolCall("1", TOOL_NAME, {"faits": []})])
    if "titre" in system:
        return FakeReply(text="« Le café du matin ».")
    if "café chaque matin" in system:
        return FakeReply(text="Tu bois du café chaque matin.")
    return FakeReply(text="Bonjour Octave, je retiens ça.")


async def drain(services: Services) -> None:
    await services.chat.drain()


def test_chat_streams_and_persists(client: TestClient, llm: dict[str, FakeProvider]) -> None:
    llm["deepseek"]._script = smart_script
    response = client.post("/v1/chat", json={"text": "Salut, je bois du café chaque matin."})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_sse(response.text)
    types = [t for t, _ in events]
    assert types[0] == "start"
    assert types[1] == "context"
    assert types[2] == "route"
    assert "delta" in types
    assert types[-1] == "done"
    reply = "".join(d["text"] for t, d in events if t == "delta")
    assert reply == "Bonjour Octave, je retiens ça."
    done = events[-1][1]
    assert done["provider"] == "deepseek"
    assert done["first_token_ms"] is not None
    conversation_id = events[0][1]["conversation_id"]

    detail = client.get(f"/v1/conversations/{conversation_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == reply
    assert detail["messages"][1]["status"] == "ok"
    assert detail["messages"][1]["trace_id"] == done["trace_id"]
    trace = client.get(f"/v1/traces/{done['trace_id']}").json()
    assert {s["kind"] for s in trace["spans"]} >= {"context", "llm"}


def test_fact_is_remembered_in_a_new_conversation(
    client: TestClient, llm: dict[str, FakeProvider], services: Services
) -> None:
    llm["deepseek"]._script = smart_script
    first = read_sse(client.post("/v1/chat", json={"text": "Je bois du café chaque matin."}).text)
    client.portal.call(drain, services)  # type: ignore[union-attr]
    memory = client.get("/v1/memory").json()
    assert [m["content"] for m in memory] == ["Octave boit du café chaque matin."]
    assert memory[0]["source"] == "conversation"
    first_id = first[0][1]["conversation_id"]
    assert client.get(f"/v1/conversations/{first_id}").json()["title"] == "Le café du matin"

    second = read_sse(
        client.post("/v1/chat", json={"text": "Qu'est-ce que je bois le matin ?"}).text
    )
    assert second[0][1]["conversation_id"] != first_id
    answer = "".join(d["text"] for t, d in second if t == "delta")
    assert answer == "Tu bois du café chaque matin."
    context = next(d for t, d in second if t == "context")
    assert any(i["kept"] and "café" in i["preview"] for i in context["items"])


def test_follow_up_keeps_the_conversation(client: TestClient) -> None:
    first = read_sse(client.post("/v1/chat", json={"text": "Bonjour"}).text)
    conversation_id = first[0][1]["conversation_id"]
    again = read_sse(
        client.post("/v1/chat", json={"text": "Encore", "conversation_id": conversation_id}).text
    )
    assert again[0][1]["conversation_id"] == conversation_id
    messages = client.get(f"/v1/conversations/{conversation_id}").json()["messages"]
    assert len(messages) == 4


def test_provider_failure_falls_back_then_errors_cleanly(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    llm["deepseek"]._script = lambda r: FakeReply(error=ProviderUnavailableError("panne"))
    llm["openrouter"]._configured = True
    events = read_sse(client.post("/v1/chat", json={"text": "Bonjour"}).text)
    route = next(d for t, d in events if t == "route")
    assert route["provider"] == "openrouter"
    assert route["fallback_from"] == "deepseek/deepseek-flash"
    assert events[-1][0] == "done"

    llm["openrouter"]._script = lambda r: FakeReply(error=ProviderUnavailableError("panne"))
    failed = read_sse(client.post("/v1/chat", json={"text": "Toujours là ?"}).text)
    assert failed[-1][0] == "error"
    assert failed[-1][1]["message"] == "Le fournisseur ne répond pas."
    conversation = client.get(f"/v1/conversations/{failed[0][1]['conversation_id']}").json()
    assert conversation["messages"][-1]["status"] == "error"
    degraded = client.get("/v1/events?type_prefix=llm.").json()
    assert {e["type"] for e in degraded} >= {"llm.fallback", "llm.provider_degraded"}


def test_chat_input_validation(client: TestClient) -> None:
    assert client.post("/v1/chat", json={"text": ""}).status_code == 422
    assert client.post("/v1/chat", json={"text": "x" * 8001}).status_code == 422
    unknown = client.post("/v1/chat", json={"text": "salut", "conversation_id": "inconnue"})
    assert unknown.status_code == 404


def test_conversation_routes(client: TestClient) -> None:
    created = client.post("/v1/conversations").json()
    assert created["title"] == "Nouvelle conversation"
    renamed = client.patch(f"/v1/conversations/{created['id']}", json={"title": "Projet"})
    assert renamed.json()["title"] == "Projet"
    assert client.get("/v1/conversations").json()[0]["id"] == created["id"]
    assert client.delete(f"/v1/conversations/{created['id']}").status_code == 204
    assert client.get(f"/v1/conversations/{created['id']}").status_code == 404


def test_memory_routes(client: TestClient) -> None:
    created = client.post("/v1/memory", json={"kind": "profil", "content": "Octave vit à Lyon."})
    assert created.status_code == 201
    item = created.json()
    assert item["source"] == "manuel"
    patched = client.patch(f"/v1/memory/{item['id']}", json={"pinned": True}).json()
    assert patched["pinned"] is True
    hits = client.get("/v1/memory/search?q=Lyon").json()
    assert hits[0]["item"]["id"] == item["id"]
    assert client.get("/v1/memory?kind=profil").json()[0]["content"] == "Octave vit à Lyon."
    stats = client.get("/v1/memory/stats").json()
    assert stats["counts"] == {"profil": 1}
    assert stats["vector_index"] == "sqlite-vec"
    assert client.post("/v1/memory", json={"kind": "humeur", "content": "x"}).status_code == 422
    assert client.delete(f"/v1/memory/{item['id']}").status_code == 204
    assert client.patch(f"/v1/memory/{item['id']}", json={"pinned": False}).status_code == 404


def test_llm_status_models_and_routing(client: TestClient) -> None:
    status = {s["provider"]: s for s in client.get("/v1/llm/status").json()}
    assert status["deepseek"]["configured"] is True
    assert status["openrouter"]["configured"] is False
    models = client.get("/v1/llm/models").json()
    assert any(m["model"] == "deepseek-flash" and m["price_in"] == 0.15 for m in models)
    ok = client.patch("/v1/settings", json={"routing": {"chat": "ollama/ministral-3:3b"}})
    assert ok.json()["routing"] == {"chat": "ollama/ministral-3:3b"}
    assert (
        client.patch("/v1/settings", json={"routing": {"inconnue": "deepseek/x"}}).status_code
        == 422
    )
    assert client.patch("/v1/settings", json={"routing": {"chat": "nimporte"}}).status_code == 422


def test_learning_can_be_turned_off(
    client: TestClient, llm: dict[str, FakeProvider], services: Services
) -> None:
    llm["deepseek"]._script = smart_script
    client.patch("/v1/settings", json={"learn_from_conversations": False})
    read_sse(client.post("/v1/chat", json={"text": "Je bois du café chaque matin."}).text)
    client.portal.call(drain, services)  # type: ignore[union-attr]
    assert client.get("/v1/memory").json() == []


def test_clean_title() -> None:
    assert clean_title("« Le café du matin ».") == "Le café du matin"
    assert clean_title('"Projet Rust"\nAutre ligne') == "Projet Rust"
    assert clean_title("   ") == ""


def test_e2e_script_is_predictable() -> None:
    chat = e2e_script(LLMRequest([Message("system", "x"), Message("user", "Salut")]))
    assert chat.text.startswith("Bien reçu : **Salut**")
    tool = e2e_script(LLMRequest([Message("user", "x")], tools=[ToolSpec("repondre", "d", {})]))
    assert tool.tool_calls[0].arguments == {"faits": []}
    title = e2e_script(LLMRequest([Message("system", "Donne un titre"), Message("user", "x")]))
    assert title.text == "Conversation de test"


def test_a_turn_can_opt_out_of_learning(
    client: TestClient, llm: dict[str, FakeProvider], services: Services
) -> None:
    llm["deepseek"]._script = smart_script

    async def voice_turn() -> None:
        events = services.chat.respond(
            text="Je bois du café chaque matin.", channel="voice", learn=False
        )
        async for _ in events:
            pass
        await services.chat.drain()

    client.portal.call(voice_turn)  # type: ignore[union-attr]
    assert client.get("/v1/memory").json() == []


def test_a_pasted_key_is_neither_stored_nor_sent(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    key = "sk-" + "b" * 40
    events = read_sse(client.post("/v1/chat", json={"text": f"Retiens ma clé {key}"}).text)
    conversation = client.get(f"/v1/conversations/{events[0][1]['conversation_id']}").json()
    assert key not in conversation["messages"][0]["content"]
    assert "***" in conversation["messages"][0]["content"]
    sent = llm["deepseek"].requests[-1][0].messages[-1].content
    assert key not in sent
    memory = client.post("/v1/memory", json={"kind": "fait", "content": f"Clé : {key}"}).json()
    assert key not in memory["content"]
