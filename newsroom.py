import os
import json
import re
import html
import hashlib
from urllib.parse import quote_plus, urlparse

import requests
import feedparser
from bs4 import BeautifulSoup

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
STATE_FILE = "state.json"
USER_AGENT = "AustraliaByAussie-Newsroom/5.0"

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
# Use a currently verified free model with structured-output support first.
# Keep OpenRouter's dynamic free router as a fallback so the workflow is not tied
# to a model that may later disappear or become paid.
OPENROUTER_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free"
]
WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

CATEGORIES = [
    "Australia", "Business", "Cost of Living", "Crime & Courts",
    "Explainers", "Life", "Politics", "World"
]

OFFICIAL_DOMAIN_SUFFIXES = (
    ".gov.au", ".nsw.gov.au", ".vic.gov.au", ".qld.gov.au",
    ".wa.gov.au", ".sa.gov.au", ".tas.gov.au", ".nt.gov.au", ".act.gov.au"
)
OFFICIAL_DOMAINS = {
    "afp.gov.au", "ato.gov.au", "accc.gov.au", "acma.gov.au", "asic.gov.au",
    "apra.gov.au", "austrac.gov.au", "abs.gov.au", "aihw.gov.au", "health.gov.au",
    "homeaffairs.gov.au", "defence.gov.au", "pm.gov.au", "treasury.gov.au",
    "finance.gov.au", "aph.gov.au", "parliament.nsw.gov.au", "police.nsw.gov.au",
    "police.vic.gov.au", "police.qld.gov.au", "police.wa.gov.au", "police.sa.gov.au",
    "police.tas.gov.au", "pfes.nt.gov.au", "police.act.gov.au", "fedcourt.gov.au",
    "hcourt.gov.au", "austlii.edu.au", "ombudsman.gov.au", "ndis.gov.au",
    "nacc.gov.au", "tga.gov.au", "foodstandards.gov.au", "rba.gov.au",
    "election.gov.au", "aec.gov.au", "servicesaustralia.gov.au", "parliament.vic.gov.au",
    "parliament.qld.gov.au", "parliament.wa.gov.au", "parliament.sa.gov.au",
    "parliament.tas.gov.au", "parliament.nt.gov.au", "parliament.act.gov.au"
}

MASTER_PROMPT = r"""
You are the senior journalist and editor for Australia By Aussie.
Write in natural Australian English.

SOURCE RULE
The supplied Guardian Australia article is the newsroom lead and the factual source for ordinary stories. Do NOT perform general web research and do NOT invent missing facts.
The Guardian is a reputable Australian news organisation. The article itself must be completely original Australia By Aussie writing: never copy, translate, or closely rewrite its wording, sentence order or paragraph structure.

OFFICIAL-SOURCE RULE
For a story identified as requiring an official/primary source, the workflow may supply one or more official Australian sources. Use those sources only to verify the specific official action/decision. Do not add unrelated facts from them.
If no official source is supplied, do not claim that a proposal, pledge, call, opinion or political argument is an official decision.

CONTENT TYPE — DO NOT CHANGE IT
- Opinion stays Opinion. Do not turn it into straight News.
- Analysis stays Analysis. Keep analysis and interpretation clearly attributed and do not present them as established facts.
- Explainer stays Explainer. Preserve its explanatory purpose and structure.
- Live stays Live/update reporting where applicable.
- Reported allegations must remain allegations and must never be written as proven facts.
- News is ordinary reported news.
- Official decision is allowed only where an identified official body actually made a decision/action.

WRITING RULES
- Include the important names, numbers, dates, locations, statements, consequences and what happens next when present in the source.
- Every paragraph must add useful information.
- No filler, speculation, invented facts or invented quotes.
- Quotes must be exact wording from the supplied source material. If exact wording is not available, paraphrase without quotation marks.
- Preserve attribution: say who made a claim, allegation, prediction or assessment.
- Do not manufacture a quote from a paraphrase.

WEBSITE
- One factual English headline, maximum 9 English words.
- Excerpt must be exactly 25 English words.
- Exactly one WordPress tag.
- Choose exactly one category from the supplied list.
- Image metadata must describe only what the supplied image actually shows.

SOCIAL
Create one detailed Facebook/Instagram post from the article, maximum 2,000 English characters including spaces. Target 1,700–1,950 where the facts allow it. It must explain the actual story, not tease it. Include an important verified quote when available. End with exactly: 👉 Have Your Say followed by one specific YES/NO question about the story.

NO VIDEO OUTPUT
Do not create video titles, voiceovers, video captions or video hashtags.

OUTPUT
Return ONLY JSON matching the supplied schema.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "story_type": {
            "type": "string",
            "enum": ["news", "official_decision", "opinion", "analysis", "explainer", "reported_allegation", "live", "other"]
        },
        "website": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "arabic_headline": {"type": "string"},
                "category": {"type": "string", "enum": CATEGORIES},
                "why": {"type": "string"},
                "excerpt": {"type": "string"},
                "arabic_excerpt": {"type": "string"},
                "tag": {"type": "string"},
                "alt_text": {"type": "string"},
                "arabic_alt_text": {"type": "string"},
                "image_title": {"type": "string"},
                "arabic_image_title": {"type": "string"},
                "caption": {"type": "string"},
                "arabic_caption": {"type": "string"},
                "description": {"type": "string"},
                "arabic_description": {"type": "string"},
                "article_html": {"type": "string"},
                "arabic_article_html": {"type": "string"}
            },
            "required": [
                "headline", "arabic_headline", "category", "why", "excerpt", "arabic_excerpt",
                "tag", "alt_text", "arabic_alt_text", "image_title", "arabic_image_title",
                "caption", "arabic_caption", "description", "arabic_description",
                "article_html", "arabic_article_html"
            ],
            "additionalProperties": False
        },
        "social": {
            "type": "object",
            "properties": {
                "english": {"type": "string"},
                "arabic": {"type": "string"}
            },
            "required": ["english", "arabic"],
            "additionalProperties": False
        }
    },
    "required": ["story_type", "website", "social"],
    "additionalProperties": False
}


def clean_text(value):
    if not value:
        return ""
    value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def article_id(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def normalise_title(title):
    value = re.sub(r"[^a-z0-9]+", " ", clean_text(title).lower())
    return re.sub(r"\s+", " ", value).strip()


def count_english_words(text):
    return re.findall(r"\b[A-Za-z][A-Za-z0-9’'-]*\b", text or "")


def http_get(url, timeout=30):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    r.raise_for_status()
    return r


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("processed", [])
            return data
    except Exception:
        pass
    return {"processed": []}


def save_state(state):
    state["processed"] = state.get("processed", [])[-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_feed():
    return feedparser.parse(http_get(GUARDIAN_RSS, timeout=45).content)


def get_article_page(url):
    r = http_get(url, timeout=45)
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content", "") if tag else ""

    title = clean_text(meta("og:title") or (soup.title.string if soup.title else ""))
    description = clean_text(meta("og:description"))
    section = clean_text(meta("article:section") or meta("og:section"))
    image_url = clean_text(meta("og:image"))

    if not image_url or "i.guim.co.uk" not in image_url:
        for img in soup.find_all("img"):
            for attr in ("src", "data-src"):
                candidate = clean_text(img.get(attr, ""))
                if "i.guim.co.uk" in candidate:
                    image_url = candidate
                    break
            if image_url and "i.guim.co.uk" in image_url:
                break

    main = soup.find("main") or soup
    for tag in main(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        tag.decompose()
    paragraphs = []
    for p in main.find_all(["p", "h2", "h3"]):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 35:
            paragraphs.append(text)
    article_text = "\n".join(paragraphs)[:18000]
    head_blob = clean_text(" ".join(paragraphs[:8]))[:5000]
    source_type = detect_source_type(url, title, head_blob, section)

    return {
        "title": title,
        "description": description,
        "section": section,
        "image_url": image_url,
        "text": article_text,
        "source_type": source_type
    }


def detect_source_type(url, title, head_blob, section):
    blob = f"{url} {title} {head_blob} {section}".lower()
    if "/commentisfree/" in url.lower() or re.search(r"\bopinion\b", blob):
        return "opinion"
    if "/analysis" in url.lower() or re.search(r"\banalysis\b", blob):
        return "analysis"
    if "/live/" in url.lower() or re.search(r"\bas it happened\b", title.lower()):
        return "live"
    if "explainer" in url.lower() or re.search(r"\bexplainer\b", blob):
        return "explainer"
    return "news"


def is_official_url(url):
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if host in OFFICIAL_DOMAINS:
        return True
    return any(host.endswith(suffix) for suffix in OFFICIAL_DOMAIN_SUFFIXES)


def resolve_result_url(url):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
        return r.url
    except Exception:
        return url


def needs_official_source(story):
    if story["source_type"] != "news":
        return False
    text = f"{story['title']}\n{story['text']}".lower()
    decision_patterns = [
        r"\bhas decided\b", r"\bdecided to\b", r"\bdecision\b", r"\brules?\b",
        r"\bruling\b", r"\bapproved\b", r"\bapproves\b", r"\brejected\b",
        r"\bpassed\b", r"\bpasses\b", r"\bsigned into law\b", r"\borders?\b",
        r"\bgovernment announced\b", r"\bminister announced\b", r"\bpolice announced\b",
        r"\bregulator announced\b", r"\bcourt (?:has )?ruled\b", r"\btakes effect\b",
        r"\bnew law\b", r"\bban takes effect\b"
    ]
    return any(re.search(pattern, text) for pattern in decision_patterns)


def official_research(title):
    query = (
        f'"{title}" site:gov.au OR site:nsw.gov.au OR site:vic.gov.au OR '
        'site:qld.gov.au OR site:wa.gov.au OR site:sa.gov.au OR site:tas.gov.au '
        'OR site:nt.gov.au OR site:act.gov.au'
    )
    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en-AU&gl=AU&ceid=AU:en"
    try:
        feed = feedparser.parse(http_get(url, timeout=35).content)
    except Exception as exc:
        print(f"Official-source discovery failed: {exc}")
        return []

    results = []
    seen = set()
    for entry in feed.entries[:10]:
        raw_url = entry.get("link", "")
        final_url = resolve_result_url(raw_url) if raw_url else ""
        if not final_url or not is_official_url(final_url) or final_url in seen:
            continue
        seen.add(final_url)
        try:
            page = get_official_page(final_url)
        except Exception as exc:
            print(f"Official page fetch failed: {final_url} | {exc}")
            continue
        results.append({
            "url": final_url,
            "source": clean_text(entry.get("source", {}).get("title", "")) if isinstance(entry.get("source", {}), dict) else "",
            "title": page["title"] or clean_text(entry.get("title", "")),
            "text": page["text"],
            "published": clean_text(entry.get("published", ""))
        })
        if len(results) >= 2:
            break
    return results


def get_official_page(url):
    r = http_get(url, timeout=35)
    soup = BeautifulSoup(r.text, "html.parser")
    title = clean_text((soup.find("meta", property="og:title") or soup.find("title")).get("content", "") if soup.find("meta", property="og:title") else (soup.title.string if soup.title else ""))
    main = soup.find("main") or soup
    for tag in main(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        tag.decompose()
    paragraphs = []
    for p in main.find_all(["p", "h1", "h2", "h3", "li"]):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 30:
            paragraphs.append(text)
    return {"title": title, "text": "\n".join(paragraphs)[:10000]}


def format_official_sources(results):
    if not results:
        return "NO OFFICIAL SOURCE FOUND. Do not call the story an official decision."
    chunks = []
    for i, item in enumerate(results, 1):
        chunks.append(
            f"OFFICIAL SOURCE {i}\nOrganisation: {item['source']}\nTitle: {item['title']}\nURL: {item['url']}\nPublished: {item['published']}\nCONTENT:\n{item['text']}"
        )
    return "\n\n".join(chunks)


def call_openrouter(user_prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://australiabyaussie.com",
        "X-Title": "Australia By Aussie Newsroom"
    }
    errors = []
    for model in OPENROUTER_MODELS:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a professional Australian news editor. Return one complete JSON object only. It MUST contain exactly these top-level objects: story_type, website, social. Never omit website or social."},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.15,
            "max_tokens": 7000,
            "reasoning": {"effort": "low", "exclude": True},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "australia_by_aussie_story",
                    "strict": True,
                    "schema": OUTPUT_SCHEMA
                }
            },
            "provider": {"require_parameters": True}
        }
        try:
            print(f"OpenRouter: {model}")
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=180)
            if r.status_code in (429, 500, 502, 503, 504):
                errors.append(f"{model}: HTTP {r.status_code}")
                continue
            if r.status_code in (400, 404, 422):
                errors.append(f"{model}: HTTP {r.status_code}: {r.text[:600]}")
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"OpenRouter authentication/permission error: {r.text[:2000]}")
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            content = (choices[0].get("message") or {}).get("content", "") if choices else ""
            if not content:
                errors.append(f"{model}: empty content")
                continue
            content = str(content).strip()
            if content.startswith("```"):
                content = re.sub(r"^```json\s*", "", content, flags=re.I)
                content = re.sub(r"\s*```$", "", content).strip()
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", content, re.S)
                if not match:
                    errors.append(f"{model}: invalid JSON")
                    continue
                result = json.loads(match.group(0))

            if not isinstance(result, dict):
                errors.append(f"{model}: JSON root is not an object")
                continue
            if "story_type" not in result or "website" not in result or "social" not in result:
                errors.append(f"{model}: incomplete schema response; missing top-level fields")
                continue

            # Defensive repair for providers that encode a nested JSON object as a string.
            for nested_key in ("website", "social"):
                if isinstance(result.get(nested_key), str):
                    try:
                        decoded = json.loads(result[nested_key])
                        if isinstance(decoded, dict):
                            result[nested_key] = decoded
                    except (TypeError, json.JSONDecodeError):
                        pass

            if not isinstance(result.get("website"), dict) or not isinstance(result.get("social"), dict):
                errors.append(f"{model}: website/social are not objects")
                continue

            print("OpenRouter model used:", data.get("model", model))
            return result
        except requests.exceptions.RequestException as exc:
            errors.append(f"{model}: {exc}")
            continue
    raise RuntimeError("All configured OpenRouter models failed: " + " | ".join(errors))


def wp_auth():
    return (WP_USERNAME, WP_APP_PASSWORD)


def get_recent_wp_posts(max_pages=3):
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    posts = []
    for page in range(1, max_pages + 1):
        r = requests.get(endpoint, auth=wp_auth(), params={"per_page": 100, "page": page, "status": "publish", "orderby": "date", "order": "desc"}, timeout=35)
        if r.status_code == 400:
            break
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        posts.extend(batch)
        if page >= int(r.headers.get("X-WP-TotalPages", page)):
            break
    return posts


def source_marker(source_url):
    return f"<!-- australia-by-aussie-source-url: {source_url} -->"


def wordpress_source_exists(source_url, posts):
    marker = source_marker(source_url)
    return any(marker in p.get("content", {}).get("rendered", "") for p in posts)


def wordpress_title_exists(title, posts):
    target = normalise_title(title)
    return bool(target) and any(normalise_title(p.get("title", {}).get("rendered", "")) == target for p in posts)


def get_or_create_term(term_type, name):
    endpoint = f"{WP_URL}/wp-json/wp/v2/{term_type}"
    r = requests.get(endpoint, auth=wp_auth(), params={"search": name, "per_page": 50}, timeout=30)
    r.raise_for_status()
    target = name.strip().lower()
    for term in r.json():
        if term.get("name", "").strip().lower() == target:
            return int(term["id"])
    r = requests.post(endpoint, auth=wp_auth(), json={"name": name}, timeout=30)
    if r.status_code == 400:
        r2 = requests.get(endpoint, auth=wp_auth(), params={"search": name, "per_page": 50}, timeout=30)
        r2.raise_for_status()
        for term in r2.json():
            if term.get("name", "").strip().lower() == target:
                return int(term["id"])
    r.raise_for_status()
    return int(r.json()["id"])


def upload_image(image_url, filename, metadata):
    r = http_get(image_url, timeout=90)
    content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    endpoint = f"{WP_URL}/wp-json/wp/v2/media"
    upload = requests.post(
        endpoint,
        auth=wp_auth(),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type
        },
        data=r.content,
        timeout=120
    )
    upload.raise_for_status()
    media_id = int(upload.json()["id"])
    meta = requests.post(
        f"{endpoint}/{media_id}",
        auth=wp_auth(),
        json={
            "alt_text": metadata["alt_text"],
            "title": metadata["image_title"],
            "caption": metadata["caption"],
            "description": metadata["description"]
        },
        timeout=30
    )
    meta.raise_for_status()
    return media_id


def publish_post(story, image_id, source_url):
    website = story["website"]
    category_id = get_or_create_term("categories", website["category"])
    tag_id = get_or_create_term("tags", website["tag"])
    content = source_marker(source_url) + "\n" + website["article_html"]
    payload = {
        "title": website["headline"],
        "content": content,
        "excerpt": website["excerpt"],
        "status": "publish",
        "categories": [category_id],
        "tags": [tag_id],
        "featured_media": image_id,
        "format": "standard"
    }
    r = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", auth=wp_auth(), json=payload, timeout=90)
    r.raise_for_status()
    return r.json()


def validate_story(story):
    website = story["website"]
    social = story["social"]
    errors = []
    if len(count_english_words(website["headline"])) > 9:
        errors.append("headline over 9 words")
    if len(count_english_words(website["excerpt"])) != 25:
        errors.append("excerpt is not exactly 25 English words")
    if website["category"] not in CATEGORIES:
        errors.append("invalid category")
    if not website["tag"].strip():
        errors.append("missing tag")
    article_text = BeautifulSoup(website["article_html"], "html.parser").get_text(" ", strip=True)
    if len(article_text) < 300:
        errors.append("article too short")
    if len(social["english"]) > 2000:
        errors.append("social post over 2000 characters")
    if "👉 Have Your Say" not in social["english"]:
        errors.append("social post missing Have Your Say")
    if not re.search(r"\b(?:YES|NO)\b", social["english"], re.I):
        errors.append("social post missing YES/NO question")
    for field in ("alt_text", "image_title", "caption", "description"):
        if not website.get(field, "").strip():
            errors.append(f"missing image {field}")
    if errors:
        raise ValueError("FINAL VALIDATION FAILED: " + "; ".join(errors))


def build_prompt(story, official_sources):
    official_text = format_official_sources(official_sources)
    return f"""{MASTER_PROMPT}

SOURCE TYPE HINT FROM GUARDIAN PAGE: {story['source_type']}
Treat this hint as authoritative unless the supplied article clearly contradicts it.

OFFICIAL-SOURCE CHECK REQUIRED: {'YES' if needs_official_source(story) else 'NO'}

GUARDIAN AUSTRALIA SOURCE
Title: {story['title']}
URL: {story['url']}
Section: {story.get('section', '')}
Description: {story.get('description', '')}

ARTICLE TEXT
{story.get('text', '')}

OFFICIAL / PRIMARY SOURCE MATERIAL (ONLY USE IF RELEVANT)
{official_text}

IMPORTANT FINAL INSTRUCTIONS
- The facts come from the supplied Guardian article, with the official source used only for the specific verified decision/action when supplied.
- Do not perform any other research.
- Do not add facts that are absent from the supplied material.
- Preserve Opinion, Analysis, Explainer, Live and allegations as their original type.
- If the source type is Opinion/Analysis/Explainer, write it in that form; do not turn it into straight news.
- If official sources are absent, do not label a proposal or political statement as an official decision.
- Use exact quotes only when their wording is present in the supplied material.
- Return ONLY the JSON object.
"""


def pick_new_candidate(feed, state, posts):
    processed = set(state.get("processed", []))
    candidates = []
    for entry in feed.entries:
        url = clean_text(entry.get("link", ""))
        title = clean_text(entry.get("title", ""))
        if not url or not title:
            continue
        aid = article_id(url)
        if aid in processed:
            continue
        if wordpress_source_exists(url, posts) or wordpress_title_exists(title, posts):
            print("Already on WordPress; marking processed:", title)
            state["processed"].append(aid)
            continue
        candidates.append({
            "id": aid,
            "url": url,
            "title": title,
            "description": clean_text(entry.get("summary", ""))
        })
    save_state(state)
    return candidates[0] if candidates else None


def main():
    print("=" * 60)
    print("Australia By Aussie Autonomous Newsroom v5")
    print("Guardian lead | original article | targeted official-source verification")
    print("One new story per run | no video generation")
    print("=" * 60)

    state = load_state()
    feed = get_feed()
    print(f"Guardian feed entries: {len(feed.entries)}")

    posts = get_recent_wp_posts(max_pages=3)
    print(f"Checked {len(posts)} recent WordPress posts for duplicates.")

    candidate = pick_new_candidate(feed, state, posts)
    if not candidate:
        print("No new unpublished Guardian story found. Exiting without an AI request.")
        return

    print("Candidate:", candidate["title"])
    try:
        page = get_article_page(candidate["url"])
        story = {**candidate, **page}
        print("Guardian source type:", story["source_type"])

        official_sources = []
        if needs_official_source(story):
            print("Official decision signals detected; checking Australian official sources only...")
            official_sources = official_research(story["title"])
            print(f"Official sources accepted: {len(official_sources)}")
        else:
            print("No official-source search needed for this story.")

        result = call_openrouter(build_prompt(story, official_sources))
        if story["source_type"] in {"opinion", "analysis", "explainer", "live"}:
            result["story_type"] = story["source_type"]

        validate_story(result)

        image_url = story["image_url"]
        if not image_url:
            raise RuntimeError("No usable Guardian source image found.")

        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", story["title"][:70]).strip("-") + ".jpg"
        print("Uploading featured image...")
        image_id = upload_image(image_url, filename, result["website"])

        print("Publishing to WordPress...")
        post = publish_post(result, image_id, candidate["url"])
        state["processed"].append(candidate["id"])
        save_state(state)
        print("PUBLISHED:", post.get("link", post.get("id")))

    except Exception as exc:
        print("Candidate failed:", exc)
        raise


if __name__ == "__main__":
    main()
