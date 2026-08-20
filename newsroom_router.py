import os
import re
from datetime import datetime, timezone, timedelta
import feedparser
import requests

import newsroom_runner as base
import groq_client
import image_selector

base.choose_clean_image = image_selector.choose_clean_image

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
BUCKETS = [1, 2, 3, 4, 6, 8, 12, 18, 24]
MAX_AGE = timedelta(hours=24)
MAX_POSTS = 2
ALLOWED_CATEGORIES = set()
SPORT_WORDS = {"sport", "sports", "australian-rules-football", "afl", "nrl", "rugby", "cricket", "football", "soccer", "tennis", "basketball", "golf", "f1", "formula-1", "motorsport"}

ROUTER_PROMPT = r"""You are the senior journalist and fact-checker for Australia By Aussie.
Write natural Australian English only. The source is Guardian Australia only. Publish only stories supplied from Guardian Australia. Write a completely original article based only on the supplied Guardian source material. Never invent facts or quotes.
Choose exactly one category from the CURRENT WORDPRESS CATEGORIES supplied at runtime. Never default to Australia. Match the category to the actual subject of the article. Never choose a sports category and never publish sports stories.
The runner will attach a separate clean image. The featured image must contain a relevant person and must directly match the article subject; for a named person, it must be that exact person. Never use logos, watermarks, screenshots, social cards, infographics or images containing overlaid text.
Return valid JSON only."""


def refresh_wordpress_categories():
    global ALLOWED_CATEGORIES
    wp_url = os.environ.get("WP_URL", "").rstrip("/")
    if not wp_url:
        raise RuntimeError("NO_PUBLICATION: WP_URL is not configured; cannot determine WordPress categories safely.")
    endpoint = f"{wp_url}/wp-json/wp/v2/categories"
    try:
        response = requests.get(endpoint, params={"per_page": 100}, timeout=30, headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"})
        response.raise_for_status()
        categories = response.json()
    except Exception as exc:
        raise RuntimeError(f"NO_PUBLICATION: Could not read current WordPress categories: {exc}") from exc

    allowed = set()
    for item in categories:
        name = str(item.get("name", "")).strip()
        slug = str(item.get("slug", "")).strip().lower()
        if not name:
            continue
        if slug in SPORT_WORDS or name.lower() in SPORT_WORDS or "sport" in slug or "sport" in name.lower():
            continue
        allowed.add(name)

    if not allowed:
        raise RuntimeError("NO_PUBLICATION: WordPress returned no non-sports categories.")
    ALLOWED_CATEGORIES = allowed
    print("CURRENT WORDPRESS CATEGORIES:", ", ".join(sorted(ALLOWED_CATEGORIES)))
    return ALLOWED_CATEGORIES


def wp_category_id(category_name):
    wp_url = os.environ.get("WP_URL", "").rstrip("/")
    if not wp_url:
        raise RuntimeError("WP_URL is not configured")
    response = requests.get(
        f"{wp_url}/wp-json/wp/v2/categories",
        params={"search": category_name, "per_page": 100},
        timeout=30,
        headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
    )
    response.raise_for_status()
    wanted = category_name.casefold()
    for item in response.json():
        if str(item.get("name", "")).strip().casefold() == wanted:
            slug = str(item.get("slug", "")).lower()
            if slug in SPORT_WORDS or "sport" in slug:
                raise RuntimeError(f"Refusing sports category: {category_name}")
            return int(item["id"])
    raise RuntimeError(f"WordPress category not found: {category_name}")


def set_post_category(post_id, category_name):
    if category_name not in ALLOWED_CATEGORIES:
        raise RuntimeError(f"Refusing to publish outside current WordPress categories: {category_name}")
    wp_url = os.environ.get("WP_URL", "").rstrip("/")
    username = os.environ.get("WP_USERNAME", "")
    app_password = os.environ.get("WP_APP_PASSWORD", "")
    if not wp_url or not username or not app_password:
        raise RuntimeError("WordPress credentials are not configured")
    category_id = wp_category_id(category_name)
    response = requests.post(
        f"{wp_url}/wp-json/wp/v2/posts/{int(post_id)}",
        auth=(username, app_password),
        json={"categories": [category_id]},
        timeout=60,
    )
    response.raise_for_status()
    updated = response.json()
    actual = updated.get("categories", [])
    if category_id not in actual:
        raise RuntimeError(f"WordPress did not confirm category assignment for post {post_id}")
    return updated


def parse_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def feed_candidates(url, source):
    now = datetime.now(timezone.utc)
    out = []
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:
        print(f"{source} RSS failed:", exc)
        return out
    for e in feed.entries:
        published = parse_time(e)
        if not published:
            continue
        age = now - published
        if age < timedelta(0) or age > MAX_AGE:
            continue
        title = base.clean_text(e.get("title", ""))
        summary = base.clean_text(e.get("summary", ""))
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue
        if not link.startswith("https://www.theguardian.com/australia-news/"):
            continue
        out.append({"id": base.article_id(link), "url": link, "title": title, "summary": summary, "published": published, "source": "Guardian Australia"})
    return out


def normalise_title(title):
    words = re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split()
    stop = {"a", "an", "the", "and", "to", "of", "in", "on", "for", "as", "is", "are", "says", "said"}
    return {w for w in words if len(w) > 2 and w not in stop}


def duplicate_story(a, b):
    if a["id"] == b["id"]:
        return True
    aa, bb = normalise_title(a["title"]), normalise_title(b["title"])
    if not aa or not bb:
        return False
    return len(aa & bb) / max(1, min(len(aa), len(bb))) >= 0.70


def priority_score(story):
    text = f"{story.get('title','')} {story.get('summary','')}".lower()
    score, matches = base.priority_score(story)
    if "anthony albanese" in text or "albanese" in text:
        score += 50
    if any(x in text for x in ("minister", "ministers", "cabinet", "treasurer", "government")):
        score += 30
    return score, matches


def discover(processed=None):
    processed = processed or set()
    items = feed_candidates(GUARDIAN_RSS, "Guardian Australia")
    items.sort(key=lambda x: x["published"], reverse=True)
    unique = []
    for item in items:
        if item["id"] in processed:
            continue
        if any(duplicate_story(item, old) for old in unique):
            continue
        unique.append(item)

    now = datetime.now(timezone.utc)
    selected = []
    seen = set()
    for hours in BUCKETS:
        bucket = [x for x in unique if x["id"] not in seen and now - x["published"] <= timedelta(hours=hours)]
        bucket.sort(key=lambda x: (priority_score(x)[0], x["published"]), reverse=True)
        print(f"TIME BUCKET {hours}h: {len(bucket)} eligible Guardian Australia stories")
        for x in bucket:
            selected.append(x)
            seen.add(x["id"])
            if len(selected) >= MAX_POSTS:
                return selected
    return selected


def _english_words(text):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.findall(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)*\b", text)


def clean_english_result(result):
    if not isinstance(result, dict):
        raise RuntimeError("NO_PUBLICATION: AI result is not a JSON object")
    website = result.get("website") or {}
    if not isinstance(website, dict):
        raise RuntimeError("NO_PUBLICATION: AI website payload is invalid")
    for key, value in website.items():
        if re.search(r"[\u0600-\u06FF]", str(value or "")):
            raise RuntimeError(f"NO_PUBLICATION: Arabic text detected in website field {key!r}.")
    category = str(website.get("category", "")).strip()
    if category not in ALLOWED_CATEGORIES:
        raise RuntimeError(f"NO_PUBLICATION: Invalid category {category!r}. Current non-sports WordPress categories: {', '.join(sorted(ALLOWED_CATEGORIES))}")
    return result


def repair_model_output(result):
    website = result.setdefault("website", {})
    social = result.setdefault("social", {})
    video = result.setdefault("video", {})
    words = _english_words(website.get("article_html", ""))
    if len(words) >= 25:
        website["excerpt"] = " ".join(words[:25])
    category_tags = {
        "Politics": "#AustralianPolitics",
        "Business": "#AustralianBusiness",
        "Cost of Living": "#CostOfLiving",
        "Crime & Courts": "#CrimeAndCourts",
        "Explainers": "#AustraliaExplained",
        "Life": "#LifeInAustralia",
        "World": "#AustraliaNews",
        "Australia": "#AustraliaNews",
    }
    preferred = category_tags.get(website.get("category"), "#AustraliaNews")
    existing = [h.strip() for h in video.get("hashtags", []) if isinstance(h, str) and h.strip()]
    hashtags = []
    for h in ["#AustraliaByAussies", preferred] + existing:
        if h not in hashtags:
            hashtags.append(h)
    if len(hashtags) < 3:
        for h in ("#AustralianNews", "#NewsAustralia", "#Australia"):
            if h not in hashtags:
                hashtags.append(h)
            if len(hashtags) == 3:
                break
    video["hashtags"] = hashtags[:3]
    facebook = social.get("english", "")
    if "👉 Have Your Say" not in facebook:
        facebook = facebook.rstrip() + "\n\n👉 Have Your Say\nDo you support this? YES or NO?"
    social["english"] = facebook[:2000]
    return result


def validate_publishable(result):
    website = result.get("website", {})
    social = result.get("social", {})
    video = result.get("video", {})
    errors = []
    headline_words = len(str(website.get("headline", "")).split())
    excerpt_count = len(_english_words(website.get("excerpt", "")))
    hashtags = video.get("hashtags", [])
    category = str(website.get("category", "")).strip()
    facebook = str(social.get("english", ""))
    if headline_words > 9:
        errors.append(f"Headline exceeds 9 words: {headline_words}")
    if excerpt_count != 25:
        errors.append(f"Excerpt is not exactly 25 English words: {excerpt_count}")
    if not website.get("tag", "").strip():
        errors.append("Missing WordPress tag")
    if category not in ALLOWED_CATEGORIES:
        errors.append(f"Invalid category: {category}")
    if len(facebook) > 2000:
        errors.append("Facebook post exceeds 2,000 characters")
    if "👉 Have Your Say" not in facebook:
        errors.append("Facebook post missing Have Your Say")
    if len(hashtags) != 3:
        errors.append(f"Hashtag count is not exactly 3: {len(hashtags)}")
    if "#AustraliaByAussies" not in hashtags:
        errors.append("Missing #AustraliaByAussies")
    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors))


def run():
    refresh_wordpress_categories()
    state = base.load_state()
    processed = set(state.get("processed", []))
    candidates = discover(processed)
    print(f"Unique fresh Guardian Australia candidates selected: {len(candidates)}")
    if not candidates:
        raise RuntimeError("NO_PUBLICATION: No new unprocessed Guardian Australia stories were found in the last 24 hours.")
    published = 0
    failures = []
    for story in candidates:
        if published >= MAX_POSTS:
            break
        print("------------------------------------------------------------")
        print("SOURCE:", story["source"])
        print("TITLE:", story["title"])
        print("AGE:", datetime.now(timezone.utc) - story["published"])
        score, matches = priority_score(story)
        print("PRIORITY:", score, matches)
        try:
            page = base.get_article_page(story["url"])
            if not page.get("text"):
                raise RuntimeError("NO_PUBLICATION: Guardian source page unavailable")
            story.update(page)
            result = groq_client.ask_groq(story, [])
            result = clean_english_result(result)
            verification = result.get("verification", {})
            if not result.get("publish", False) or verification.get("status") == "INSUFFICIENT VERIFIED INFORMATION":
                raise RuntimeError(f"NO_PUBLICATION: model approval failed: {verification.get('status')}")
            result = repair_model_output(result)
            validate_publishable(result)
            image_url, image_credit = base.choose_clean_image(story, result)
            website = result["website"]
            filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + ".jpg"
            media_id = base.upload_external_image(image_url, filename, website["alt_text"], image_credit)
            post = base.publish_post(website, media_id)
            if not post.get("id") or post.get("status") != "publish":
                raise RuntimeError(f"WORDPRESS_PUBLISH_FAILED: WordPress did not confirm publish. id={post.get('id')!r}, status={post.get('status')!r}")
            post = set_post_category(post["id"], website["category"])
            if post.get("status") != "publish":
                raise RuntimeError("WORDPRESS_PUBLISH_FAILED: category update did not return a published post")
            print("PUBLISHED SUCCESSFULLY")
            print("SOURCE:", story["source"])
            print("CATEGORY:", website.get("category"))
            print("IMAGE:", image_credit)
            print("URL:", post.get("link"))
            print("POST ID:", post.get("id"))
            processed.add(story["id"])
            published += 1
            state["processed"] = list(processed)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            base.save_state(state)
        except Exception as exc:
            message = f"{story['title']} -> {exc}"
            failures.append(message)
            print("SKIPPED:", story["title"])
            print("Reason:", exc)
            continue
    print("============================================================")
    print(f"RUN COMPLETE: published {published}/{MAX_POSTS} articles.")
    print(f"FAILED/SKIPPED: {len(failures)}")
    for failure in failures:
        print("FAILURE:", failure)
    if published == 0:
        raise RuntimeError("NO_PUBLICATION: 0 articles were confirmed published to WordPress. See FAILURE lines above.")
    return True


if __name__ == "__main__":
    run()
