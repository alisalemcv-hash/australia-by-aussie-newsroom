import os
import json
import re
import time
import requests
import newsroom_runner as base

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

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


def ask_groq(story, sources=None):
    sources = sources or []
    source_text = "\n\n".join(
        f"SOURCE {s.get('number','')}\nTitle: {s.get('title','')}\nURL: {s.get('url','')}\nPublished: {s.get('published','')}\nSummary: {s.get('summary','')}"
        for s in sources
    ) or "No additional source results were supplied."

    user_prompt = f"""
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

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.2,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"}
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "AustraliaByAussie-Newsroom/1.0"
    }

    for attempt in range(3):
        try:
            response = requests.post(ENDPOINT, headers=headers, json=payload, timeout=180)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"Groq temporary HTTP {response.status_code}; retrying in {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
                text = re.sub(r"\s*```$", "", text).strip()
            result = json.loads(text)
            print("Groq model:", data.get("model", GROQ_MODEL))
            return result
        except requests.exceptions.RequestException as exc:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Groq API request failed: {exc}") from exc
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("Groq returned an invalid newsroom response.") from exc

    raise RuntimeError("Groq request failed after retries.")
