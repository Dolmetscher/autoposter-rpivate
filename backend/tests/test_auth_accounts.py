async def test_register_login_me(client):
    r = await client.post("/api/web/auth/register",
                          json={"email": "a@b.io", "password": "secret123"})
    assert r.status_code == 201, r.text
    r = await client.post("/api/web/auth/login",
                          json={"email": "a@b.io", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    r = await client.get("/api/web/auth/me",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.io"


async def test_wrong_password(client):
    await client.post("/api/web/auth/register",
                      json={"email": "a@b.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "a@b.io", "password": "wrongpass"})
    assert r.status_code == 401


async def test_me_requires_auth(client):
    r = await client.get("/api/web/auth/me")
    assert r.status_code == 401


async def test_sources_list(client, auth_headers):
    r = await client.get("/api/web/sources", headers=auth_headers)
    assert r.status_code == 200
    srcs = {s["id"]: s for s in r.json()["list"]}
    assert srcs[1]["name"] == "ВКонтакте"
    assert srcs[99]["implemented"] is True
    assert srcs[3]["implemented"] is True   # Facebook — реализован
    assert srcs[10]["implemented"] is True  # Вордпресс — реализован
    assert srcs[4]["implemented"] is True   # Twitter/X — реализован (API v2)
    assert srcs[12]["implemented"] is True  # Тредс — реализован
    assert srcs[19]["implemented"] is True  # Тамблер — реализован
    assert srcs[7]["implemented"] is True   # Вайбер — заглушка с понятной ошибкой
    assert srcs[16]["experimental"] is True # Вотсап — экспериментальная заглушка


async def test_connect_mock_account(client, auth_headers):
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {"nickname": "x"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["pages"]) == 3

    r = await client.get("/api/web/accounts", headers=auth_headers)
    accounts = r.json()["list"]
    assert len(accounts) == 1
    assert accounts[0]["source_id"] == 99
