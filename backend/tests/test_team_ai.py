"""Тесты команд/ролей, тарифов-лимитов и AI-помощника."""
import pytest


async def _register_and_login(client, email):
    await client.post("/api/web/auth/register",
                      json={"email": email, "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_team_roles_flow(client, auth_headers, mock_pages):
    # владелец подключил песочницу (mock_pages); приглашаем редактора
    editor = await _register_and_login(client, "editor@t.io")
    viewer = await _register_and_login(client, "viewer@t.io")

    r = await client.post("/api/web/team/invite", headers=auth_headers,
                          json={"email": "editor@t.io", "role": "editor"})
    assert r.status_code == 201, r.text
    member_id = r.json()["id"]

    # редактор видит данные владельца
    r = await client.get("/api/web/accounts", headers=editor)
    assert r.status_code == 200
    assert len(r.json()["list"]) == 1
    assert r.json()["list"][0]["source_id"] == 99

    # редактор может создать пост
    r = await client.post("/api/web/posts", headers=editor, json={
        "texts": [{"text": "Пост от редактора"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text

    # редактору нельзя: настройки, аккаунты (мутации), команда
    r = await client.put("/api/web/settings", headers=editor, json={"timezone": "UTC"})
    assert r.status_code == 403
    r = await client.post("/api/web/accounts/connect", headers=editor,
                          json={"source_id": 99, "credentials": {}})
    assert r.status_code == 403
    r = await client.post("/api/web/team/invite", headers=editor,
                          json={"email": "x@t.io", "role": "viewer"})
    assert r.status_code == 403

    # наблюдатель: только чтение
    await client.post("/api/web/team/invite", headers=auth_headers,
                      json={"email": "viewer@t.io", "role": "viewer"})
    r = await client.get("/api/web/posts", headers=viewer)
    assert r.status_code == 200
    r = await client.post("/api/web/posts", headers=viewer, json={
        "texts": [{"text": "нельзя"}], "page_ids": mock_pages[:1],
        "publication_when_type": 1})
    assert r.status_code == 403

    # смена роли и удаление участника
    r = await client.put(f"/api/web/team/{member_id}", headers=auth_headers,
                         json={"role": "admin"})
    assert r.json()["role"] == "admin"
    r = await client.delete(f"/api/web/team/{member_id}", headers=auth_headers)
    assert r.json()["success"] is True
    # после удаления редактор видит уже только свои (пустые) данные
    r = await client.get("/api/web/accounts", headers=editor)
    assert r.json()["list"] == []


async def test_invitation_requires_registered_user(client, auth_headers):
    r = await client.post("/api/web/team/invite", headers=auth_headers,
                          json={"email": "nobody@t.io", "role": "editor"})
    assert r.status_code == 404


async def test_plans_and_limits(client, auth_headers):
    r = await client.get("/api/web/settings/plans", headers=auth_headers)
    assert r.status_code == 200
    plans = r.json()["plans"]
    assert set(plans.keys()) == {"free", "pro", "business"}
    assert r.json()["current"] == "free"

    # free: лимит 3 аккаунта — четвёртый не подключается
    for i in range(3):
        r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                              json={"source_id": 99, "credentials": {}})
        assert r.status_code == 200, r.text
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {}})
    assert r.status_code == 400
    assert "Лимит тарифа" in r.json()["detail"]

    # повышение тарифа снимает лимит
    r = await client.post("/api/web/settings/plan", headers=auth_headers,
                          json={"plan": "pro"})
    assert r.json()["current"] == "pro"
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {}})
    assert r.status_code == 200

    # неизвестный тариф
    r = await client.post("/api/web/settings/plan", headers=auth_headers,
                          json={"plan": "gold"})
    assert r.status_code == 400


async def test_ai_generate_and_hashtags(client, auth_headers, monkeypatch):
    async def fake_post_chat(llm_config, system_prompt, text):
        if "Подбери" in system_prompt:
            return {"choices": [{"message": {"content": "#новости #сми"}}]}
        return {"choices": [{"message": {"content": f"Сгенерированный пост про {text}"}}]}

    from app.services import rewrite as rewrite_service
    monkeypatch.setattr(rewrite_service, "_post_chat", fake_post_chat)

    # без настроенного LLM — 400
    r = await client.post("/api/web/ai/generate", headers=auth_headers,
                          json={"topic": "кофе"})
    assert r.status_code == 400

    await client.put("/api/web/settings", headers=auth_headers, json={
        "llm_config": {"base_url": "http://llm.test/v1", "api_key": "k", "model": "m"}})

    r = await client.post("/api/web/ai/generate", headers=auth_headers,
                          json={"topic": "новая кофейня", "style": "дружелюбно"})
    assert r.status_code == 200
    assert r.json()["text"] == "Сгенерированный пост про новая кофейня"

    r = await client.post("/api/web/ai/hashtags", headers=auth_headers,
                          json={"text": "Текст поста"})
    assert r.json()["hashtags"] == "#новости #сми"


async def test_ai_best_hours(client, auth_headers, mock_pages):
    await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    from app.db import SessionFactory
    from app.services import stats as stats_service
    async with SessionFactory() as s:
        await stats_service.update_stats(s)
        await s.commit()

    r = await client.get("/api/web/ai/best-hours", headers=auth_headers)
    assert r.status_code == 200
    hours = r.json()["hours"]
    assert isinstance(hours, list) and 1 <= len(hours) <= 3
    assert all(0 <= h <= 23 for h in hours)
