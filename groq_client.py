import os
import json
import re
import time
import requests
import newsroom_runner as base

# Provider order: Groq -> Gemini -> Mistral -> OpenRouter.
# The first provider that successfully returns valid JSON is used for the article.
PROVIDERS = ["groq", "gemini", "mistral", "openrouter"]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "").strip()
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MISTRAL_ENDPOINT = "https://api.mistral.ai/v1/chat/completions"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = r"""
You are the senior journalist and fact-checker for Australia By Aussie.
Write natural Australian English only.

The supplied source article is a lead, not text to copy. Write a completely original article.
Use only facts supported by the supplied source material. Never invent facts, statistics, quotes, names or events.
If the supplied material is not sufficient to safely publish, set publish=false.

IMPORTANT CONTENT RULES:
- English only. Never output Arabic anywhere.
- Australian news only.
- Preserve the nature of the source. If it is Opinion, keep it Opinion. If Analysis, keep Analysis. If Explainer, keep Explainer. Do not turn these into straight News.
- Official decisions, government announcements and formal statements should be reported as such.
- Prefer primary/official facts contained in the supplied material when available.
- Do not claim independent verification that was not supplied.

CATEGORY: choose exactly ONE of:
Australia, Politics, Business, Cost of Living, Life, World, Finance.
Choose based on the subject of the story. Albanese, ministers, cabinet, parliament, federal government, Labor/Coalition and political disputes normally belong in Politics. Use Australia for general Australian news that does not fit another category.

HEADLINE: maximum 9 words.
EXCERPT: exactly 25 English words.
TAG: exactly one relevant tag.
ARTICLE: complete original English article in HTML.
SOCIAL: English only, maximum 2,000 characters, ending with "👉 Have Your Say" and one specific YES/NO question.

Return JSON only with this structure:
{
  "verification": {"status":"VERIFIED|PARTIALLY VERIFIED|DEVELOPING|INSUFFICIENT VERIFIED INFORMATION", "confirmed":[], "not_confirmed":[]},
  "publish": true,
  "website": {
    "headline":"",
    "category":"",
    "why":"",
    "excerpt":"",
    "tag":"",
    "alt_text":"",
    "image_title":"",
    "caption":"",
    "description":"",
    "article_html":""
  },
  "social": {"english":""},
  "video": {"title":"", "voiceover":"", "caption":"", "hashtags":[]}
}

Do not add any other keys.
"""


def build_user_prompt(story, sources):
    source_text = "\n\n".join(
        f"SOURCE {s.get('number','')}\nTitle: {s.get('title','')}\nURL: {s.get('url','')}\nPublished: {s.get('published','')}\nSummary: {s.get('summary','')}"
        for s in sources
    ) or "No additional source results were supplied."

    return f"""
{PROMPT}

PRIMARY SOURCE
Title: {story.get('title','')}
URL: {story.get('url','')}
Description: {story.get('description','')}
Published: {story.get('published','')}

SOURCE ARTICLE TEXT:
{story.get('text','')}

ADDITIONAL SOURCE MATERIAL:
{source_text}

Return the JSON object now.
"""


def _clean_json_text(text):
    if isinstance(text, list):
        text = "".join(
            (part.get("text", "") if isinstance(part, dict) else str(part))
            for part in text
        )
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _parse_openai_style(data, provider):
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{provider} returned no message content") from exc
    text = _clean_json_text(content)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"{provider} returned JSON that is not an object")
    print(f"AI provider used: {provider} | model: {data.get('model', 'unknown')}")
    return result


def _post_openai_style(endpoint, key, model, user_prompt, provider):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.2,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "AustraliaByAussie-Newsroom/1.0",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://australiabyaussie.com"
        headers["X-Title"] = "Australia By Aussie Newsroom"

    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                print(f"{provider} temporary HTTP {response.status_code}; retrying once...")
                time.sleep(5)
                continue
            response.raise_for_status()
            return _parse_openai_style(response.json(), provider)
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(3)
                continue
            raise RuntimeError(f"{provider} API request failed: {exc}") from exc
    raise RuntimeError(f"{provider} API request failed: {last_error}")


def _ask_gemini(user_prompt):
    url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL)
    params = {"key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 12000,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AustraliaByAussie-Newsroom/1.0",
    }
    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(url, params=params, headers=headers, json=payload, timeout=180)
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                print(f"gemini temporary HTTP {response.status_code}; retrying once...")
                time.sleep(5)
                continue
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = _clean_json_text(text)
            result = json.loads(text)
            if not isinstance(result, dict):
                raise RuntimeError("gemini returned JSON that is not an object")
            print(f"AI provider used: gemini | model: {GEMINI_MODEL}")
            return result
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(3)
                continue
            raise RuntimeError(f"gemini API request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("gemini returned an invalid newsroom response") from exc
    raise RuntimeError(f"gemini API request failed: {last_error}")


def _provider_call(provider, user_prompt):
    if provider == "groq":
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not configured")
        return _post_openai_style(GROQ_ENDPOINT, GROQ_API_KEY, GROQ_MODEL, user_prompt, provider)
    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        return _ask_gemini(user_prompt)
    if provider == "mistral":
        if not MISTRAL_API_KEY:
            raise RuntimeError("MISTRAL_API_KEY is not configured")
        return _post_openai_style(MISTRAL_ENDPOINT, MISTRAL_API_KEY, MISTRAL_MODEL, user_prompt, provider)
    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        return _post_openai_style(OPENROUTER_ENDPOINT, OPENROUTER_API_KEY, OPENROUTER_MODEL, user_prompt, provider)
    raise RuntimeError(f"Unknown AI provider: {provider}")


def ask_groq(story, sources=None):
    """Backward-compatible entry point used by newsroom_router.py.

    Despite the historical function name, this now performs automatic provider
    failover in the configured order and returns the first valid newsroom JSON.
    """
    sources = sources or []
    user_prompt = build_user_prompt(story, sources)
    errors = []

    for provider in PROVIDERS:
        try:
            print(f"Trying AI provider: {provider}")
            return _provider_call(provider, user_prompt)
        except Exception as exc:
            message = str(exc)
            errors.append(f"{provider}: {message}")
            print(f"AI provider failed: {provider}: {message}")
            continue

    raise RuntimeError("All configured AI providers failed: " + " | ".join(errors))
