import os, re, json
from datetime import datetime, timezone, timedelta

source = open("newsroom.py", "r", encoding="utf-8").read()
ns = {"__name__": "newsroom_loaded"}
exec(compile(source, "newsroom.py", "exec"), ns)

clean_text = ns["clean_text"]
article_id = ns["article_id"]
load_state = ns["load_state"]
save_state = ns["save_state"]
get_article_page = ns["get_article_page"]
ask_openrouter_original = ns["ask_openrouter"]
validate_story = ns["validate_story"]
upload_image = ns["upload_image"]
publish_post = ns["publish_post"]

GUARDIAN_RSS_FEEDS = [
    "https://www.theguardian.com/australia-news/rss",
]
MAX_AGE = timedelta(hours=24)
MAX_POSTS_PER_RUN = 10

ENGLISH_ONLY_PROMPT = r"""
You are the senior journalist and fact-checker for Australia By Aussie.

ABSOLUTE LANGUAGE RULE:
EVERY PUBLISHABLE FIELD MUST BE ENGLISH ONLY.
Use natural Australian English.
NEVER write Arabic. Do not translate anything into Arabic.
Do not output Arabic text in any field.

SOURCE RULE:
The ONLY news source permitted for this newsroom run is The Guardian Australia,
from https://www.theguardian.com/australia-news/ .
Do not use or cite Google News, non-Guardian media, or Guardian sections outside Australia news.
The supplied Guardian Australia article is the sole source/lead.

ARTICLE RULE:
Write a completely original Australia By Aussie article in English.
Do not copy or closely rewrite The Guardian. Use verified facts from the supplied Guardian Australia article.
Do not invent facts or quotes.

CATEGORY:
Use ONLY the category Australia.

HEADLINE:
Maximum 9 English words.

EXCERPT:
Exactly 25 English words.

TAG:
Exactly one relevant English WordPress tag.

IMAGE:
Use the supplied Guardian Australia image. The image must be clean and free of visible logos, watermarks, captions, or overlaid text where possible.

WEBSITE ARTICLE:
Complete English article only.

FACEBOOK/INSTAGRAM:
English only, maximum 2,000 characters, ending with 👉 Have Your Say and one YES/NO question.

VIDEO:
English only.

If the source is not clearly an Australia news story from The Guardian Australia, set publish=false.

Return valid JSON only.
"""

ns["MASTER_PROMPT"] = ENGLISH_ONLY_PROMPT

# Priority is applied BEFORE the AI writes the article.
# Higher score = publish earlier in the run.
PRIORITY_RULES = [
    (120, ("anthony albanese", "albanese", "prime minister", "prime-minister")),
    (105, ("minister", "ministers", "cabinet", "ministerial")),
    (95, (
        "treasurer", "finance minister", "foreign minister", "defence minister",
        "health minister", "education minister", "environment minister",
        "attorney-general", "attorney general", "home affairs minister",
        "immigration minister", "housing minister", "social services minister",
        "transport minister", "communications minister", "industry minister",
        "energy minister", "resources minister", "agriculture minister",
        "employment minister", "skills minister", "assistant minister"
    )),
    (85, ("federal government", "australian government", "government announces", "government says", "government plans")),
    (75, ("politics", "political", "labor", "coalition", "parliament", "senate", "house of representatives")),
]


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def discover_guardian_stories_last_24h():
    import requests, feedparser
    now = datetime.now(timezone.utc)
    candidates = {}
    for feed_url in GUARDIAN_RSS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                timeout=30,
                headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            print("Guardian Australia RSS discovery failed:", exc)
            continue

        for entry in feed.entries:
            published = parse_entry_time(entry)
            if not published:
                continue
            age = now - published
            if age < timedelta(0) or age > MAX_AGE:
                continue

            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            if not link.startswith("https://www.theguardian.com/australia-news/"):
                continue

            key = article_id(link)
            candidates[key] = {
                "id": key,
                "url": link,
                "title": title,
                "summary": summary,
                "published": published,
            }

    return list(candidates.values())


def priority_score(story):
    text = f"{story.get('title', '')} {story.get('summary', '')}".lower()
    score = 0
    matched = []
    for points, keywords in PRIORITY_RULES:
        if any(keyword in text for keyword in keywords):
            score = max(score, points)
            matched.extend(keyword for keyword in keywords if keyword in text)
    return score, matched


def contains_arabic(value):
    return bool(re.search(r"[\u0600-\u06FF]", str(value or "")))


def clean_english_result(result):
    website = result.get("website", {})
    for key in list(website.keys()):
        if key.startswith("arabic_") or key in {"ar...", "arabic_article_html"}:
            website.pop(key, None)
    for key, value in website.items():
        if contains_arabic(value):
            raise RuntimeError(f"NO_PUBLICATION: Arabic text detected in website field {key!r}.")
    if website.get("category") != "Australia":
        raise RuntimeError(f"NO_PUBLICATION: Invalid category {website.get('category')!r}; only Australia is allowed.")
    return result


def run_one():
    state = load_state()
    processed = set(state.get("processed", []))
    candidates = discover_guardian_stories_last_24h()

    # First priority: Albanese / Prime Minister / ministers / cabinet / federal politics.
    # Second priority: all other eligible Guardian Australia stories, newest first.
    for story in candidates:
        score, matched = priority_score(story)
        story["priority_score"] = score
        story["priority_matches"] = matched

    candidates.sort(
        key=lambda item: (
            item["priority_score"],
            item["published"],
        ),
        reverse=True,
    )

    unprocessed = [c for c in candidates if c["id"] not in processed]
    print(f"Found {len(candidates)} Guardian Australia candidates from the last 24 hours.")
    print(f"Unprocessed candidates: {len(unprocessed)}")
    print(f"Target per run: {MAX_POSTS_PER_RUN} articles.")

    published_count = 0
    attempted_count = 0

    for story in unprocessed:
        if published_count >= MAX_POSTS_PER_RUN:
            break

        attempted_count += 1
        print("------------------------------------------------------------")
        print("Candidate priority score:", story["priority_score"])
        print("Priority matches:", ", ".join(story["priority_matches"]) or "general Australia news")
        print("Selected Guardian Australia story:", story["title"])

        try:
            page = get_article_page(story["url"])
            if not page.get("text"):
                raise RuntimeError("NO_PUBLICATION: Guardian Australia source page was unavailable.")
            story.update(page)

            image_url = story.get("image_url", "").strip()
            if not image_url:
                raise RuntimeError("NO_PUBLICATION: No Guardian Australia source image was found.")
            print("Guardian Australia image found:", image_url)

            result = ask_openrouter_original(story, [])
            result = clean_english_result(result)
            verification = result.get("verification", {})
            status = verification.get("status", "")
            print("Verification status:", status)

            if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
                raise RuntimeError(
                    f"NO_PUBLICATION: Story was not approved for publishing. "
                    f"publish={result.get('publish', False)}, status={status!r}"
                )

            validate_story(result)
            website = result["website"]
            filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + ".jpg"
            media_id = upload_image(image_url, filename, website["alt_text"])
            print("Image uploaded. Media ID:", media_id)

            post = publish_post(website, media_id)
            post_id = post.get("id")
            post_status = post.get("status")
            if not post_id or post_status != "publish":
                raise RuntimeError(
                    f"WORDPRESS_PUBLISH_FAILED: WordPress did not confirm a published post. "
                    f"id={post_id!r}, status={post_status!r}"
                )

            print("PUBLISHED SUCCESSFULLY")
            print("Title:", post.get("title", {}).get("rendered"))
            print("URL:", post.get("link"))
            print("Post ID:", post_id)

            processed.add(story["id"])
            state["processed"] = list(processed)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            published_count += 1

        except Exception as exc:
            # One bad story must not stop the remaining nine slots in this run.
            print("SKIPPED CANDIDATE:", story["title"])
            print("Reason:", exc)
            continue

    print("============================================================")
    print(f"RUN COMPLETE: published {published_count}/{MAX_POSTS_PER_RUN} articles.")
    print(f"Attempted candidates: {attempted_count}")

    # Do not fail the scheduled workflow just because there were fewer than 10
    # eligible stories or one source/model response was rejected.
    return True


if __name__ == "__main__":
    run_one()
