import os
import json
import re
import html
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# AUSTRALIA BY AUSSIE — AUTOMATED NEWSROOM
# OPENROUTER FREE EDITION
# ============================================================

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

STATE_FILE = "state.json"

# ============================================================
# OPENROUTER
# ============================================================

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

# Official OpenRouter free router.
# It automatically selects an available free model.
OPENROUTER_MODEL = "openrouter/free"

# ============================================================
# WORDPRESS
# ============================================================

WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]


# ============================================================
# MASTER PROMPT
# ============================================================

MASTER_PROMPT = r"""
🇦🇺 AUSTRALIA BY AUSSIE
MASTER NEWSROOM PROMPT 2026

You are the senior journalist, researcher and fact-checker for Australia By Aussie.

Write in natural Australian English.

PRIORITY:

SOURCE ACCURACY
→ VERIFIED FACTS
→ ORIGINAL WRITING
→ IMPORTANT DETAILS
→ CLEAR PRESENTATION

RESEARCH

The supplied Guardian article is ONLY a lead.

You have also been provided with additional live source results discovered
from Google News RSS.

Use the supplied source material to verify the story.

Prioritise:

• Government
• Police
• Courts
• Regulators
• Official organisations
• Official statements
• Direct statements
• Reputable Australian media
• Original / primary sources

Whenever a source result points to an official or primary source,
treat that source as more authoritative than a secondary media report.

Do not assume that the Guardian article is correct simply because it is supplied.

Verify all important:

• Names
• Ages
• Dates
• Times
• Locations
• Numbers
• Statistics
• Dollar amounts
• Events
• Statements
• Quotes
• Current status
• What happens next

Do not include information simply because it appears in one source.

If an important claim cannot be reliably verified, do not present it as fact.

SOURCE HANDLING

The source material may contain:

• Duplicate reports
• Old information
• Conflicting information
• Opinions
• Commentary
• Unverified claims

Resolve conflicts using the strongest available source.

If sources disagree and the disagreement cannot be resolved,
state the uncertainty clearly.

Do not invent missing information.

ORIGINAL ARTICLE

Write a completely ORIGINAL Australia By Aussie article.

Do not copy, translate or closely rewrite any source.

Use verified facts and write the story independently.

Include everything a reader genuinely needs to understand the story.

Do NOT add:

• Filler
• Generic introductions
• Repeated information
• Unnecessary background
• Speculation
• Unverified claims

Every paragraph must provide useful information.

QUOTES

Use important VERIFIED direct quotes when available.

Quotes must come from the supplied source material.

Use exact verified wording.

Clearly identify who said it.

Never invent, reconstruct or alter a quote.

If exact wording cannot be verified,
paraphrase it without quotation marks.

VERIFICATION

Give:

VERIFICATION STATUS:
VERIFIED / PARTIALLY VERIFIED / DEVELOPING / INSUFFICIENT VERIFIED INFORMATION

If the story is not sufficiently verified:

INSUFFICIENT VERIFIED INFORMATION — DO NOT PUBLISH

CATEGORY

Use ONLY ONE:

Australia
Business
Cost of Living
Crime & Courts
Explainers
Life
Politics
World

WEBSITE

HEADLINE
Maximum 9 English words.

CATEGORY
One approved category.

WHY
One short explanation.

EXCERPT
EXACTLY 25 ENGLISH WORDS.

TAG
Exactly ONE relevant WordPress tag.

IMAGE

Provide:

ALT TEXT
TITLE
CAPTION
DESCRIPTION

ARTICLE

Complete original English article.

Then complete Arabic translation.

FACEBOOK / INSTAGRAM

Maximum 2,000 English characters INCLUDING spaces.

Target 1,700–1,950 characters.

The reader should understand the actual story without opening the website.

End with:

👉 Have Your Say

Then ONE specific YES / NO question.

VIDEO

VIDEO TITLE

VOICEOVER

Complete news voiceover based only on verified information.

VIDEO CAPTION

Immediately followed by exactly three hashtags.

One MUST be:

#AustraliaByAussies

FINAL RULE

NO FILLER.
NO SPECULATION.
NO INVENTED FACTS.
NO INVENTED QUOTES.
NO COPIED ARTICLES.

EXCERPT = EXACTLY 25 ENGLISH WORDS.

FACEBOOK / INSTAGRAM = MAXIMUM 2,000 ENGLISH CHARACTERS.
"""


# ============================================================
# HELPERS
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {"processed": []}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return {"processed": []}

        data.setdefault(
            "processed",
            []
        )

        return data

    except Exception:

        return {"processed": []}


def save_state(state):

    state["processed"] = (
        state.get("processed", [])[-200:]
    )

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


def clean_text(value):

    if not value:
        return ""

    value = BeautifulSoup(
        str(value),
        "html.parser"
    ).get_text(
        " ",
        strip=True
    )

    value = html.unescape(value)

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def article_id(url):

    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:20]


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    timeout=30,
    headers=None,
    allow_redirects=True
):

    default_headers = {
        "User-Agent":
            "AustraliaByAussie-Newsroom/1.0"
    }

    if headers:
        default_headers.update(
            headers
        )

    response = requests.get(
        url,
        timeout=timeout,
        headers=default_headers,
        allow_redirects=allow_redirects
    )

    response.raise_for_status()

    return response


# ============================================================
# GUARDIAN RSS
# ============================================================

def get_feed():

    response = http_get(
        GUARDIAN_RSS,
        timeout=30
    )

    return feedparser.parse(
        response.content
    )


# ============================================================
# GOOGLE NEWS RSS SOURCE DISCOVERY
# ============================================================

def search_google_news(query):

    """
    Free source discovery using Google News RSS.

    This does NOT use the paid OpenRouter web-search feature.
    """

    encoded_query = quote_plus(
        query
    )

    url = (
        f"{GOOGLE_NEWS_RSS}"
        f"?q={encoded_query}"
        f"&hl=en-AU"
        f"&gl=AU"
        f"&ceid=AU:en"
    )

    try:

        response = http_get(
            url,
            timeout=30
        )

        feed = feedparser.parse(
            response.content
        )

    except Exception as e:

        print(
            "Google News search failed:",
            str(e)
        )

        return []

    results = []

    for entry in feed.entries[:10]:

        title = clean_text(
            entry.get(
                "title",
                ""
            )
        )

        link = (
            entry.get(
                "link",
                ""
            )
            .strip()
        )

        summary = clean_text(
            entry.get(
                "summary",
                ""
            )
        )

        published = clean_text(
            entry.get(
                "published",
                ""
            )
        )

        if not title or not link:
            continue

        results.append(
            {
                "title": title,
                "url": link,
                "summary": summary,
                "published": published
            }
        )

    return results


# ============================================================
# SOURCE PAGE
# ============================================================

def get_source_page(url):

    try:

        response = http_get(
            url,
            timeout=30
        )

    except Exception as e:

        print(
            "Could not open source:",
            url
        )

        return {
            "title": "",
            "description": "",
            "text": ""
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = ""
    description = ""

    og_title = soup.find(
        "meta",
        property="og:title"
    )

    og_description = soup.find(
        "meta",
        property="og:description"
    )

    if og_title:

        title = og_title.get(
            "content",
            ""
        )

    if og_description:

        description = og_description.get(
            "content",
            ""
        )

    paragraphs = []

    for p in soup.find_all("p"):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if text and len(text) > 40:

            paragraphs.append(
                text
            )

    page_text = "\n".join(
        paragraphs[:50]
    )

    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "text": page_text
    }


# ============================================================
# GUARDIAN ARTICLE PAGE
# ============================================================

def get_article_page(url):

    response = http_get(
        url,
        timeout=30
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = ""
    description = ""
    image_url = ""

    og_title = soup.find(
        "meta",
        property="og:title"
    )

    og_description = soup.find(
        "meta",
        property="og:description"
    )

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_title:

        title = og_title.get(
            "content",
            ""
        )

    if og_description:

        description = og_description.get(
            "content",
            ""
        )

    if og_image:

        image_url = og_image.get(
            "content",
            ""
        )

    if not image_url:

        image = soup.find(
            "meta",
            attrs={
                "name": "twitter:image"
            }
        )

        if image:

            image_url = image.get(
                "content",
                ""
            )

    paragraphs = []

    for p in soup.find_all("p"):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if text and len(text) > 40:

            paragraphs.append(
                text
            )

    page_text = "\n".join(
        paragraphs[:80]
    )

    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "image_url": image_url,
        "text": page_text
    }


# ============================================================
# BUILD ADDITIONAL SOURCES
# ============================================================

def collect_sources(story):

    print(
        "Searching free Google News sources..."
    )

    query = story["title"]

    results = search_google_news(
        query
    )

    print(
        f"Found {len(results)} additional source results."
    )

    sources = []

    # Avoid sending too much data to the model.
    for index, result in enumerate(
        results[:8],
        start=1
    ):

        source = {
            "number": index,
            "title": result["title"],
            "url": result["url"],
            "published": result["published"],
            "summary": result["summary"]
        }

        sources.append(
            source
        )

    return sources


def format_sources(sources):

    if not sources:
        return (
            "No additional Google News "
            "source results were available."
        )

    blocks = []

    for source in sources:

        blocks.append(
            f"""
SOURCE {source["number"]}

Title:
{source["title"]}

URL:
{source["url"]}

Published:
{source["published"]}

Summary:
{source["summary"]}
""".strip()
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# OPENROUTER
# ============================================================

def ask_openrouter(
    story,
    sources
):

    endpoint = (
        "https://openrouter.ai/api/v1/"
        "chat/completions"
    )

    source_text = format_sources(
        sources
    )

    prompt = f"""
{MASTER_PROMPT}

IMPORTANT AUTOMATION RULES:

1. The supplied Guardian article is ONLY a lead.
2. Additional source results were discovered through Google News RSS.
3. Compare the supplied sources before writing.
4. Prefer official and primary sources.
5. Do not assume a claim is true merely because multiple media outlets repeat it.
6. Never invent a quote.
7. Only use direct quotes whose wording appears in the supplied source material.
8. If there is not enough reliable information, set:
   "publish": false
9. If important facts conflict and cannot be resolved, do not publish.
10. Return ONLY valid JSON.
11. Do not wrap JSON in Markdown fences.
12. Do not include commentary outside the JSON object.

PRIMARY LEAD:

Title:
{story["title"]}

URL:
{story["url"]}

Description:
{story["description"]}

Source page text:
{story["text"]}

ADDITIONAL LIVE SOURCE DISCOVERY:

{source_text}

Return exactly this JSON structure:

{{
  "verification": {{
    "status": "",
    "confirmed": [],
    "not_confirmed": []
  }},

  "publish": true,

  "website": {{
    "headline": "",
    "arabic_headline": "",
    "category": "",
    "why": "",
    "excerpt": "",
    "arabic_excerpt": "",
    "tag": "",
    "alt_text": "",
    "arabic_alt_text": "",
    "image_title": "",
    "arabic_image_title": "",
    "caption": "",
    "arabic_caption": "",
    "description": "",
    "arabic_description": "",
    "article_html": "",
    "arabic_article_html": ""
  }},

  "social": {{
    "english": "",
    "arabic": ""
  }},

  "video": {{
    "title": "",
    "arabic_title": "",
    "voiceover": "",
    "arabic_voiceover": "",
    "caption": "",
    "hashtags": []
  }}
}}

The article_html must contain the complete original English article.

The arabic_article_html must contain the complete Arabic translation.

The English Facebook/Instagram post MUST be under 2,000 characters including spaces.

The excerpt MUST contain exactly 25 English words.

The hashtags array MUST contain exactly 3 hashtags.

One hashtag MUST be:

#AustraliaByAussies

Do not put hashtags anywhere else.

Do not add information that cannot be verified.
"""

    # --------------------------------------------------------
    # JSON SCHEMA
    # --------------------------------------------------------

    schema = {
        "type": "object",
        "properties": {

            "verification": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string"
                    },
                    "confirmed": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "not_confirmed": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": [
                    "status",
                    "confirmed",
                    "not_confirmed"
                ],
                "additionalProperties": False
            },

            "publish": {
                "type": "boolean"
            },

            "website": {
                "type": "object",
                "properties": {

                    "headline": {
                        "type": "string"
                    },

                    "arabic_headline": {
                        "type": "string"
                    },

                    "category": {
                        "type": "string"
                    },

                    "why": {
                        "type": "string"
                    },

                    "excerpt": {
                        "type": "string"
                    },

                    "arabic_excerpt": {
                        "type": "string"
                    },

                    "tag": {
                        "type": "string"
                    },

                    "alt_text": {
                        "type": "string"
                    },

                    "arabic_alt_text": {
                        "type": "string"
                    },

                    "image_title": {
                        "type": "string"
                    },

                    "arabic_image_title": {
                        "type": "string"
                    },

                    "caption": {
                        "type": "string"
                    },

                    "arabic_caption": {
                        "type": "string"
                    },

                    "description": {
                        "type": "string"
                    },

                    "arabic_description": {
                        "type": "string"
                    },

                    "article_html": {
                        "type": "string"
                    },

                    "arabic_article_html": {
                        "type": "string"
                    }
                },

                "required": [
                    "headline",
                    "arabic_headline",
                    "category",
                    "why",
                    "excerpt",
                    "arabic_excerpt",
                    "tag",
                    "alt_text",
                    "arabic_alt_text",
                    "image_title",
                    "arabic_image_title",
                    "caption",
                    "arabic_caption",
                    "description",
                    "arabic_description",
                    "article_html",
                    "arabic_article_html"
                ],

                "additionalProperties": False
            },

            "social": {
                "type": "object",
                "properties": {
                    "english": {
                        "type": "string"
                    },
                    "arabic": {
                        "type": "string"
                    }
                },
                "required": [
                    "english",
                    "arabic"
                ],
                "additionalProperties": False
            },

            "video": {
                "type": "object",
                "properties": {

                    "title": {
                        "type": "string"
                    },

                    "arabic_title": {
                        "type": "string"
                    },

                    "voiceover": {
                        "type": "string"
                    },

                    "arabic_voiceover": {
                        "type": "string"
                    },

                    "caption": {
                        "type": "string"
                    },

                    "hashtags": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    }
                },

                "required": [
                    "title",
                    "arabic_title",
                    "voiceover",
                    "arabic_voiceover",
                    "caption",
                    "hashtags"
                ],

                "additionalProperties": False
            }
        },

        "required": [
            "verification",
            "publish",
            "website",
            "social",
            "video"
        ],

        "additionalProperties": False
    }

    payload = {

        "model": OPENROUTER_MODEL,

        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],

        # OpenRouter structured output.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "australia_by_aussie_newsroom",
                "strict": True,
                "schema": schema
            }
        },

        "temperature": 0.2,

        "max_tokens": 16000,

        "provider": {
            "require_parameters": True
        }
    }

    headers = {
        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}",

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "https://australiabyaussie.com",

        "X-Title":
            "Australia By Aussie Newsroom"
    }

    print(
        "Sending story to OpenRouter Free..."
    )

    # --------------------------------------------------------
    # RETRY ONLY FOR TEMPORARY SERVER / RATE LIMIT ERRORS
    # --------------------------------------------------------

    max_attempts = 3

    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=180
            )

            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                if attempt < max_attempts:

                    wait_seconds = (
                        5 * attempt
                    )

                    print(
                        f"OpenRouter temporary error "
                        f"{response.status_code}. "
                        f"Retrying in "
                        f"{wait_seconds}s..."
                    )

                    time.sleep(
                        wait_seconds
                    )

                    continue

            response.raise_for_status()

            break

        except requests.exceptions.HTTPError as e:

            try:

                error_data = (
                    response.json()
                )

                error_message = json.dumps(
                    error_data,
                    ensure_ascii=False
                )

            except Exception:

                error_message = (
                    response.text[:5000]
                )

            raise RuntimeError(
                f"OpenRouter API HTTP error "
                f"{response.status_code}:\n"
                f"{error_message}"
            ) from e

        except requests.exceptions.RequestException as e:

            if attempt < max_attempts:

                wait_seconds = (
                    5 * attempt
                )

                print(
                    "OpenRouter request failed. "
                    f"Retrying in {wait_seconds}s..."
                )

                time.sleep(
                    wait_seconds
                )

                continue

            raise RuntimeError(
                f"OpenRouter API request failed: {e}"
            ) from e

    data = response.json()

    # --------------------------------------------------------
    # EXTRACT RESPONSE
    # --------------------------------------------------------

    try:

        message = (
            data["choices"][0]["message"]
        )

        text = message.get(
            "content",
            ""
        )

    except Exception:

        raise RuntimeError(
            "OpenRouter returned an unexpected response:\n"
            + json.dumps(
                data,
                ensure_ascii=False
            )[:6000]
        )

    if not text:

        raise RuntimeError(
            "OpenRouter returned empty content:\n"
            + json.dumps(
                data,
                ensure_ascii=False
            )[:6000]
        )

    text = text.strip()

    # Safety fallback.
    if text.startswith("```"):

        text = re.sub(
            r"^```json\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

        text = text.strip()

    try:

        result = json.loads(
            text
        )

    except json.JSONDecodeError as e:

        raise RuntimeError(
            "OpenRouter returned invalid JSON.\n\n"
            + text[:6000]
        ) from e

    # Print the actual model selected by the router.
    print(
        "OpenRouter model used:",
        data.get(
            "model",
            "unknown"
        )
    )

    return result


# ============================================================
# WORDPRESS
# ============================================================

def wp_auth():

    return (
        WP_USERNAME,
        WP_APP_PASSWORD
    )


def upload_image(
    image_url,
    filename,
    alt_text
):

    response = http_get(
        image_url,
        timeout=60
    )

    content_type = response.headers.get(
        "Content-Type",
        "image/jpeg"
    )

    if "image" not in content_type:

        content_type = "image/jpeg"

    media_endpoint = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    upload = requests.post(
        media_endpoint,
        auth=wp_auth(),

        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"',

            "Content-Type":
                content_type
        },

        data=response.content,
        timeout=90
    )

    upload.raise_for_status()

    media = upload.json()

    media_id = media["id"]

    metadata_response = requests.post(
        f"{media_endpoint}/{media_id}",
        auth=wp_auth(),

        json={
            "alt_text": alt_text
        },

        timeout=30
    )

    metadata_response.raise_for_status()

    return media_id


def publish_post(
    content,
    featured_media
):

    endpoint = (
        f"{WP_URL}/wp-json/wp/v2/posts"
    )

    response = requests.post(
        endpoint,
        auth=wp_auth(),

        json={
            "title": content["headline"],
            "content": content["article_html"],
            "excerpt": content["excerpt"],
            "status": "publish",
            "featured_media": featured_media
        },

        timeout=90
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# VALIDATION
# ============================================================

def count_words(text):

    return re.findall(
        r"\b[\w’'-]+\b",
        text,
        flags=re.UNICODE
    )


def validate_story(result):

    website = result.get(
        "website",
        {}
    )

    social = result.get(
        "social",
        {}
    )

    video = result.get(
        "video",
        {}
    )

    errors = []

    headline = website.get(
        "headline",
        ""
    )

    excerpt = website.get(
        "excerpt",
        ""
    )

    tag = website.get(
        "tag",
        ""
    )

    category = website.get(
        "category",
        ""
    )

    facebook = social.get(
        "english",
        ""
    )

    hashtags = video.get(
        "hashtags",
        []
    )

    allowed_categories = {
        "Australia",
        "Business",
        "Cost of Living",
        "Crime & Courts",
        "Explainers",
        "Life",
        "Politics",
        "World"
    }

    if len(
        headline.split()
    ) > 9:

        errors.append(
            "Headline exceeds 9 words."
        )

    if len(
        count_words(excerpt)
    ) != 25:

        errors.append(
            "Excerpt is not exactly 25 words: "
            f"{len(count_words(excerpt))}"
        )

    if not tag:

        errors.append(
            "Missing WordPress tag."
        )

    if category not in allowed_categories:

        errors.append(
            f"Invalid category: {category}"
        )

    if len(facebook) > 2000:

        errors.append(
            "Facebook post exceeds 2,000 "
            f"characters: {len(facebook)}"
        )

    if "👉 Have Your Say" not in facebook:

        errors.append(
            "Facebook post is missing "
            "Have Your Say."
        )

    if len(hashtags) != 3:

        errors.append(
            "Hashtag count is not exactly 3."
        )

    if "#AustraliaByAussies" not in hashtags:

        errors.append(
            "Missing #AustraliaByAussies."
        )

    if errors:

        raise ValueError(
            "Validation failed:\n"
            + "\n".join(errors)
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=============================================="
    )

    print(
        "Australia By Aussie Automated Newsroom"
    )

    print(
        "OPENROUTER FREE"
    )

    print(
        "=============================================="
    )

    print(
        "OpenRouter model:",
        OPENROUTER_MODEL
    )

    state = load_state()

    processed = set(
        state.get(
            "processed",
            []
        )
    )

    print(
        "Checking Guardian Australia RSS..."
    )

    feed = get_feed()

    if not feed.entries:

        print(
            "No Guardian stories found."
        )

        return

    candidates = []

    for entry in feed.entries[:25]:

        url = entry.get(
            "link",
            ""
        ).strip()

        if not url:
            continue

        key = article_id(
            url
        )

        if key in processed:
            continue

        title = clean_text(
            entry.get(
                "title",
                ""
            )
        )

        description = clean_text(
            entry.get(
                "summary",
                ""
            )
        )

        candidates.append(
            {
                "id": key,
                "url": url,
                "title": title,
                "description": description
            }
        )

    if not candidates:

        print(
            "No new stories."
        )

        return

    print(
        f"Found {len(candidates)} new candidates."
    )

    # --------------------------------------------------------
    # ONE STORY PER RUN
    # --------------------------------------------------------

    story = candidates[0]

    print(
        "Selected:"
    )

    print(
        story["title"]
    )

    print(
        "Opening source page..."
    )

    page = get_article_page(
        story["url"]
    )

    story.update(
        page
    )

    if not story.get(
        "image_url"
    ):

        print(
            "No image found. Skipping story."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(
            state
        )

        return

    print(
        "Image found."
    )

    print(
        story["image_url"]
    )

    # --------------------------------------------------------
    # FREE SOURCE DISCOVERY
    # --------------------------------------------------------

    sources = collect_sources(
        story
    )

    # --------------------------------------------------------
    # OPENROUTER
    # --------------------------------------------------------

    result = ask_openrouter(
        story,
        sources
    )

    verification = result.get(
        "verification",
        {}
    )

    status = verification.get(
        "status",
        ""
    )

    print(
        "Verification status:",
        status
    )

    if not result.get(
        "publish",
        False
    ):

        print(
            "Story was not approved for publishing."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(
            state
        )

        return

    if status == (
        "INSUFFICIENT VERIFIED INFORMATION"
    ):

        print(
            "Insufficient verified information. "
            "Not publishing."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(
            state
        )

        return

    # --------------------------------------------------------
    # VALIDATE BEFORE PUBLISHING
    # --------------------------------------------------------

    validate_story(
        result
    )

    website = result["website"]

    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    print(
        "Uploading source image to WordPress..."
    )

    extension = ".jpg"

    try:

        head_response = requests.head(
            story["image_url"],
            timeout=30,

            headers={
                "User-Agent":
                    "AustraliaByAussie-Newsroom/1.0"
            },

            allow_redirects=True
        )

        content_type = head_response.headers.get(
            "Content-Type",
            ""
        )

    except Exception:

        content_type = ""

    if "png" in content_type.lower():

        extension = ".png"

    elif "webp" in content_type.lower():

        extension = ".webp"

    filename = (
        re.sub(
            r"[^a-zA-Z0-9]+",
            "-",
            website["headline"]
        )
        .strip("-")
        .lower()
        + extension
    )

    media_id = upload_image(
        story["image_url"],
        filename,
        website["alt_text"]
    )

    print(
        "Image uploaded. Media ID:",
        media_id
    )

    # --------------------------------------------------------
    # WORDPRESS
    # --------------------------------------------------------

    print(
        "Publishing WordPress article..."
    )

    post = publish_post(
        website,
        media_id
    )

    print(
        "=============================================="
    )

    print(
        "PUBLISHED SUCCESSFULLY"
    )

    print(
        "=============================================="
    )

    print(
        "Title:",
        post.get(
            "title",
            {}
        ).get(
            "rendered"
        )
    )

    print(
        "URL:",
        post.get(
            "link"
        )
    )

    print(
        "Post ID:",
        post.get(
            "id"
        )
    )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    processed.add(
        story["id"]
    )

    state["processed"] = list(
        processed
    )

    state["last_run"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    save_state(
        state
    )

    print(
        "Newsroom state saved."
    )


if __name__ == "__main__":

    main()
