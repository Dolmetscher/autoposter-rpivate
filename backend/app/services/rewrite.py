"""Рерайт текстов через LLM (любой OpenAI-совместимый провайдер:
OpenAI, YandexGPT, GigaChat через совместимый шлюз, vLLM, Ollama...)."""
import logging

import httpx

log = logging.getLogger("rewrite")


async def _post_chat(llm_config: dict, system_prompt: str, text: str) -> dict:
    base_url = llm_config["base_url"].rstrip("/")
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {llm_config['api_key']}"},
            json={
                "model": llm_config.get("model") or "gpt-4o-mini",
                "temperature": 0.9,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()


async def rewrite_text(llm_config: dict, text: str) -> tuple[str, str]:
    """Возвращает (новый_текст, сообщение_об_ошибке). Без настроек — текст без изменений."""
    if not llm_config or not llm_config.get("base_url") or not llm_config.get("api_key"):
        return text, "LLM не настроен — текст без изменений"
    prompt = llm_config.get("prompt") or None
    if not prompt:
        from ..config import settings
        prompt = settings.default_rewrite_prompt
    try:
        data = await _post_chat(llm_config, prompt, text)
        new_text = (data["choices"][0]["message"]["content"] or "").strip()
        return (new_text or text), ""
    except Exception as e:
        log.warning("rewrite failed: %s", e)
        return text, f"Рерайт не удался ({e}) — опубликован оригинал"
