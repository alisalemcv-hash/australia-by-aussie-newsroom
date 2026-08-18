import os
import json
import re
import html
import hashlib
import time
from urllib.parse import quote_plus, urlparse

import requests
import feedparser
from bs4 import BeautifulSoup

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
STATE_FILE = "state.json"

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
# Keep the previously working free model first. The larger GPT-OSS model was
# noticeably slower for this newsroom workflow.
OPENROUTER_MODELS = ["dots-studio/dots-3-note-preview:free", "openrouter/free"]
WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
CATEGORIES = ["Australia", "Business", "Cost of Living", "Crime & Courts", "Explainers", "Life", "Politics", "World"]

# Additional research is intentionally NOT used for ordinary stories.
# It is used only when the story is classified as an official decision or another
# story type where an official/primary source is specifically needed.
OFFICIAL_DOMAIN_SUFFIXES = (
    ".gov.au", ".nsw.gov.au", ".vic.gov.au", ".qld.gov.au", ".wa.gov.au",
    ".sa.gov.au", ".tas.gov.au", ".nt.gov.au", ".act.gov.au"
)
OFFICIAL_DOMAINS = {
    "afp.gov.au", "ato.gov.au", "accc.gov.au", "acma.gov.au", "asic.gov.au",
    "apra.gov.au", "austrac.gov.au", "abs.gov.au", "aihw.gov.au", "health.gov.au",
    "homeaffairs.gov.au", "defence.gov.au", "pm.gov.au", "treasury.gov.au",
    "finance.gov.au", "aph.gov.au", "parliament.nsw.gov.au", "police.nsw.gov.au",
    "police.vic.gov.au", "police.qld.gov.au", "police.wa.gov.au", "police.sa.gov.au",
    "police.tas.gov.au", "pfes.nt.gov.au", "police.act.gov.au", "courtsofaustralia.com.au",
    "fedcourt.gov.au", "hcourt.gov.au", "austlii.edu.au", "ombudsman.gov.au",
    "ndis.gov.au", "nacc.gov.au", "tga.gov.au", "foodstandards.gov.au",
    "climatechangeauthority.gov.au", "productivity.gov.au", "rba.gov.au", "election.gov.au",
    "aec.gov.au", "servicesaustralia.gov.au"
}

MASTER_PROMPT = r"""
You are the senior journalist and editor for Australia By Aussie.
Write in natural Australian English.

The supplied Guardian Australia story is the newsroom lead and is a reputable Australian news source.
For ordinary stories, DO NOT perform additional research. Use the supplied Guardian story as the factual source and write a completely original Australia By Aussie article from it.

Additional research is allowed ONLY when this workflow supplies an official/primary source because the story is an official decision or another case that genuinely requires an official source.

EDITORIAL PRIORITIES:
1. Accurate reporting.
2. Correct attribution.
3. Completely original wording and structure.
4. Important names, numbers, dates, locations and next steps.
5. Preserve the actual nature of the source story.
6. No filler, speculation, invented facts or invented quotes.

STORY TYPE — CRITICAL:
- OPINION must remain Opinion. Do not rewrite an opinion piece as straight news.
- ANALYSIS must remain Analysis. Do not present analysis or interpretation as established news fact.
- EXPLAINER must remain Explainer. Do not turn an explainer into a breaking-news report.
- OFFICIAL DECISION must remain an official decision only when the source actually reports a decision made by an identified official body. A proposal, pledge, call, opinion or political argument is NOT automatically an official decision.
- NEWS is ordinary reported news.
- REPORTED_ALLEGATION must clearly attribute allegations and must never state an allegation as proven fact.
- LIVE must remain a live/update story where applicable.

ORIGINALITY:
The article must be original journalism-style writing. Do not copy, translate, or closely rewrite the Guardian wording, sentence order or paragraph structure. Use the source facts and write independently.

SOURCE HANDLING:
- The Guardian story is the supplied source for ordinary news.
- Do not invent facts merely to make the article longer.
- If the source does not establish something, do not state it as established fact.
- Never turn an allegation into a proven fact.
- Never turn a journalist's interpretation into a fact.
- Never turn opinion/commentary/analysis/explainer into straight news.
- For official decisions, state the exact decision and identify the organisation that made it.
- Quotes must use exact wording present in the supplied source material or supplied official source. Never reconstruct a quote.
- If exact wording is unavailable, paraphrase without quotation marks.

IMAGE:
- Describe only what the supplied image evidence actually shows.
- Never remove, crop around, or bypass a publisher watermark or branding.
"""

OUTPUT_SCHEMA = {"type":"object","properties":{
"story_type":{"type":"string","enum":["news","official_decision","opinion","analysis","explainer","reported_allegation","live","other"]},
"website":{"type":"object","properties":{
"headline":{"type":"string"},"arabic_headline":{"type":"string"},"category":{"type":"string","enum":CATEGORIES},"why":{"type":"string"},"excerpt":{"type":"string"},"arabic_excerpt":{"type":"string"},"tag":{"type":"string"},"alt_text":{"type":"string"},"arabic_alt_text":{"type":"string"},"image_title":{"type":"string"},"arabic_image_title":{"type":"string"},"caption":{"type":"string"},"arabic_caption":{"type":"string"},"description":{"type":"string"},"arabic_description":{"type":"string"},"article_html":{"type":"string"},"arabic_article_html":{"type":"string"}},"required":["headline","arabic_headline","category","why","excerpt","arabic_excerpt","tag","alt_text","arabic_alt_text","image_title","arabic_image_title","caption","arabic_caption","description","arabic_description","article_html","arabic_article_html"],"additionalProperties":False},
"social":{"type":"object","properties":{"english":{"type":"string"},"arabic":{"type":"string"}},"required":["english","arabic"],"additionalProperties":False}},"required":["story_type","website","social"],"additionalProperties":False}

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
    return re.findall(r"\b[A-Za-z][A-Za-z0-9’'-]*\b", text)

def http_get(url, timeout=30, headers=None):
    h = {"User-Agent": "AustraliaByAussie-Newsroom/4.0"}
    if headers:
        h.update(headers)
    r = requests.get(url, timeout=timeout, headers=h, allow_redirects=True)
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

def clean_image_url(url):
    return url or ""

def get_article_page(url):
    r = http_get(url, timeout=45)
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content", "") if tag else ""

    title = clean_text(meta("og:title") or (soup.title.string if soup.title else ""))
    description = clean_text(meta("og:description"))
    section = clean_text(meta("article:section") or meta("og:section"))
    image_url = clean_image_url(meta("og:image"))
    candidates = []
    for img in soup.find_all("img"):
        for attr in ("src", "data-src"):
            v = img.get(attr)
            if v and "i.guim.co.uk" in v:
                candidates.append(v)
        srcset = img.get("srcset", "")
        if srcset:
            candidates.extend(x.strip().split(" ")[0] for x in srcset.split(",") if x.strip())
    for candidate in candidates:
        if clean_image_url(candidate):
            image_url = clean_image_url(candidate)
            break

    main = soup.find("main") or soup
    for tag in main(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        tag.decompose()
    paragraphs = []
    for p in main.find_all(["p", "h2", "h3"]):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 35:
            paragraphs.append(text)
    return {"title": title, "description": description, "section": section, "image_url": image_url, "text": "\n".join(paragraphs)[:30000]}

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
        r = requests.get(url, timeout=20, headers={"User-Agent": "AustraliaByAussie-Newsroom/4.0"}, allow_redirects=True)
        return r.url
    except Exception:
        return url

def official_research(title, story_text):
    query = f'"{title}" site:gov.au OR site:nsw.gov.au OR site:vic.gov.au OR site:qld.gov.au OR site:wa.gov.au OR site:sa.gov.au OR site:tas.gov.au OR site:nt.gov.au OR site:act.gov.au'
    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en-AU&gl=AU&ceid=AU:en"
    try:
        feed = feedparser.parse(http_get(url, timeout=45).content)
    except Exception as exc:
        print(f"Official-source search failed: {exc}")
        return []
    results = []
    for entry in feed.entries[:8]:
        raw_url = entry.get("link", "")
        final_url = resolve_result_url(raw_url) if raw_url else ""
        if not final_url or not is_official_url(final_url):
            continue
        source = entry.get("source", {})
        results.append({"title": clean_text(entry.get("title", "")), "url": final_url, "source": clean_text(source.get("title", "")) if isinstance(source, dict) else "", "published": clean_text(entry.get("published", "")), "summary": clean_text(entry.get("summary", ""))[:1800]})
        if len(results) >= 3:
            break
    return results

def format_research(results):
    if not results:
        return "No official/primary source was found. Do not invent one."
    return "\n\n".join(f"[{i}] {r['source']} | {r['title']}\nURL: {r['url']}\nPublished: {r['published']}\nSummary: {r['summary']}" for i, r in enumerate(results, 1))

def call_openrouter(user_prompt, schema, max_tokens=9000):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://australiabyaussie.com", "X-Title": "Australia By Aussie Newsroom"}
    errors = []
    for model in OPENROUTER_MODELS:
        payload = {"model": model, "messages": [{"role": "system", "content": "You are a professional Australian news editor. Return only valid JSON."}, {"role": "user", "content": user_prompt}], "temperature": 0.15, "max_tokens": max_tokens, "response_format": {"type": "json_schema", "json_schema": {"name": "australia_by_aussie_story", "strict": True, "schema": schema}}, "provider": {"require_parameters": True}}
        for attempt in range(1, 3):
            try:
                print(f"OpenRouter: {model} (attempt {attempt}/2)")
                r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=180)
                if r.status_code in (400, 404, 422):
                    errors.append(f"{model}: HTTP {r.status_code}: {r.text[:500]}")
                    break
                if r.status_code in (429, 500, 502, 503, 504):
                    errors.append(f"{model}: HTTP {r.status_code}")
                    if attempt == 1:
                        time.sleep(4)
                        continue
                    break
                if r.status_code in (401, 403):
                    raise RuntimeError(f"OpenRouter authentication/permission error: {r.text[:2000]}")
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") or []
                content = (choices[0].get("message") or {}).get("content", "") if choices else ""
                if not content:
                    errors.append(f"{model}: empty content")
                    break
                content = str(content).strip()
                if content.startswith("```"):
                    content = re.sub(r"^```json\s*", "", content, flags=re.I)
                    content = re.sub(r"\s*```$", "", content).strip()
                try:
                    result = json.loads(content)
                    print("OpenRouter model used:", data.get("model", model))
                    return result
                except json.JSONDecodeError:
                    match = re.search(r"\{.*\}", content, re.S)
                    if match:
                        return json.loads(match.group(0))
                    errors.append(f"{model}: invalid JSON")
                    break
            except requests.exceptions.RequestException as exc:
                errors.append(f"{model}: {exc}")
                if attempt == 1:
                    time.sleep(4)
                    continue
                break
    raise RuntimeError("All configured free OpenRouter models failed. " + " | ".join(errors))

def wp_auth(): return (WP_USERNAME, WP_APP_PASSWORD)

def get_recent_wp_posts(max_pages=10):
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    posts = []
    for page in range(1, max_pages + 1):
        r = requests.get(endpoint, auth=wp_auth(), params={"per_page": 100, "page": page, "status": "publish"}, timeout=45)
        if r.status_code == 400: break
        r.raise_for_status()
        batch = r.json()
        if not batch: break
        posts.extend(batch)
        if page >= int(r.headers.get("X-WP-TotalPages", page)): break
    return posts

def source_marker(source_url): return f"<!-- australia-by-aussie-source-url: {source_url} -->"
def wordpress_source_exists(source_url, posts): return any(source_marker(source_url) in p.get("content", {}).get("rendered", "") for p in posts)
def wordpress_title_exists(title, posts):
    target = normalise_title(title)
    return bool(target) and any(normalise_title(p.get("title", {}).get("rendered", "")) == target for p in posts)

def get_or_create_term(term_type, name):
    endpoint = f"{WP_URL}/wp-json/wp/v2/{term_type}"
    r = requests.get(endpoint, auth=wp_auth(), params={"search": name, "per_page": 50}, timeout=30); r.raise_for_status()
    target = name.strip().lower()
    for term in r.json():
        if term.get("name", "").strip().lower() == target: return int(term["id"])
    r = requests.post(endpoint, auth=wp_auth(), json={"name": name}, timeout=30)
    if r.status_code == 400:
        r2 = requests.get(endpoint, auth=wp_auth(), params={"search": name, "per_page": 50}, timeout=30); r2.raise_for_status()
        for term in r2.json():
            if term.get("name", "").strip().lower() == target: return int(term["id"])
    r.raise_for_status(); return int(r.json()["id"])

def upload_image(image_url, filename, metadata):
    r = http_get(image_url, timeout=90)
    content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"): content_type = "image/jpeg"
    endpoint = f"{WP_URL}/wp-json/wp/v2/media"
    upload = requests.post(endpoint, auth=wp_auth(), headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": content_type}, data=r.content, timeout=120)
    upload.raise_for_status(); media_id = int(upload.json()["id"])
    meta = requests.post(f"{endpoint}/{media_id}", auth=wp_auth(), json={"alt_text": metadata["alt_text"], "title": metadata["image_title"], "caption": metadata["caption"], "description": metadata["description"]}, timeout=30)
    meta.raise_for_status(); return media_id

def publish_post(story, image_id, source_url):
    website = story["website"]
    category_id = get_or_create_term("categories", website["category"]); tag_id = get_or_create_term("tags", website["tag"])
    payload = {"title": website["headline"], "content": source_marker(source_url) + "\n" + website["article_html"], "excerpt": website["excerpt"], "status": "publish", "categories": [category_id], "tags": [tag_id], "featured_media": image_id, "format": "standard"}
    r = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", auth=wp_auth(), json=payload, timeout=90); r.raise_for_status(); return r.json()

def validate_story(story):
    website = story["website"]; social = story["social"]; errors = []
    if len(count_english_words(website["headline"])) > 9: errors.append("headline over 9 words")
    if len(count_english_words(website["excerpt"])) != 25: errors.append("excerpt is not exactly 25 English words")
    if website["category"] not in CATEGORIES: errors.append("invalid category")
    if not website["tag"].strip(): errors.append("missing tag")
    if len(BeautifulSoup(website["article_html"], "html.parser").get_text(" ", strip=True)) < 300: errors.append("article too short")
    if len(social["english"]) > 2000: errors.append("social post over 2000 characters")
    if "👉 Have Your Say" not in social["english"]: errors.append("social post missing Have Your Say")
    if not re.search(r"\b(?:YES|NO)\b", social["english"], re.I): errors.append("social post missing yes/no question")
    for field in ("alt_text", "image_title", "caption", "description"):
        if not website.get(field, "").strip(): errors.append(f"missing image {field}")
    if errors: raise ValueError("FINAL VALIDATION FAILED: " + "; ".join(errors))

def build_prompt(story, official_sources=None):
    official_sources = official_sources or []
    research_text = format_research(official_sources) if official_sources else "NO ADDITIONAL RESEARCH — write from the supplied Guardian story only."
    return f"""{MASTER_PROMPT}

WORKFLOW RULE:
For this story, the supplied Guardian Australia article is the factual source. Do NOT do general web research.
Only use the official-source material below if it is present. It is supplied only because the story requires an official/primary source.

IMPORTANT:
- Do not change Opinion into News.
- Do not change Analysis into News.
- Do not change Explainer into News.
- Do not call something an official decision merely because a politician proposed, promised, urged or criticised something.
- An official decision requires an actual decision/action by the identified official body.
- For an allegation, clearly attribute it and avoid language that treats it as proven.

Return ONLY JSON matching the schema.

GUARDIAN AUSTRALIA STORY
Title: {story['title']}
URL: {story['url']}
Guardian section: {story.get('section','')}
Description: {story.get('description','')}

ARTICLE TEXT:
{story.get('text','')}

OFFICIAL / PRIMARY SOURCE MATERIAL:
{research_text}
"""

def pick_candidates(feed, state):
    processed = set(state.get("processed", [])); candidates = []
    for entry in feed.entries:
        url = entry.get("link", "").strip(); title = clean_text(entry.get("title", ""))
        if not url or not title: continue
        aid = article_id(url)
        if aid in processed: continue
        candidates.append({"id": aid, "url": url, "title": title, "description": clean_text(entry.get("summary", ""))})
    return candidates

def main():
    print("=" * 52); print("Australia By Aussie Automated Newsroom"); print("Guardian lead | original writing | targeted official-source check"); print("=" * 52)
    state = load_state(); feed = get_feed(); candidates = pick_candidates(feed, state)
    print(f"Found {len(candidates)} new Guardian candidates.")
    if not candidates: print("No new stories to publish."); return
    wp_posts = get_recent_wp_posts(max_pages=10); print(f"Checked {len(wp_posts)} published WordPress posts for duplicates.")
    for candidate in candidates[:10]:
        try:
            print(f"\nCandidate: {candidate['title']}")
            if wordpress_source_exists(candidate["url"], wp_posts):
                print("Already published by source URL. Skipping before AI/research."); state["processed"].append(candidate["id"]); save_state(state); continue
            page = get_article_page(candidate["url"]); story = {**candidate, **page}
            if wordpress_title_exists(page["title"] or candidate["title"], wp_posts):
                print("Already published by matching title. Skipping before AI/research."); state["processed"].append(candidate["id"]); save_state(state); continue
            print("Writing and classifying from Guardian source only...")
            result = call_openrouter(build_prompt(story), OUTPUT_SCHEMA); story_type = result.get("story_type", "news"); print("Story type:", story_type)
            if story_type == "official_decision":
                print("Official decision detected. Searching only for an official/primary source...")
                official_sources = official_research(page["title"] or candidate["title"], page.get("text", "")); print(f"Found {len(official_sources)} official/primary sources.")
                result = call_openrouter(build_prompt(story, official_sources), OUTPUT_SCHEMA); print("Story type after official-source review:", result.get("story_type"))
            validate_story(result)
            image_url = page["image_url"]
            if not image_url: raise RuntimeError("No usable source image found.")
            filename = re.sub(r"[^A-Za-z0-9._-]+", "-", page["title"][:70]).strip("-") + ".jpg"
            print("Uploading source image to WordPress..."); image_id = upload_image(image_url, filename, result["website"])
            print("Publishing to WordPress..."); post = publish_post(result, image_id, candidate["url"])
            state["processed"].append(candidate["id"]); save_state(state); wp_posts.append(post); print("Published:", post.get("link", post.get("id"))); break
        except Exception as exc:
            print("Candidate failed:", exc); continue

if __name__ == "__main__": main()
