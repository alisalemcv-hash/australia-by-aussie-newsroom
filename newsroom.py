import os
import json
import re
import html
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# AUSTRALIA BY AUSSIE — AUTOMATED NEWSROOM V2
# PROFESSIONAL VERIFICATION EDITION
# ============================================================

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

STATE_FILE = "state.json"

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_MODEL = "openrouter/free"

WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]


# ============================================================
# MASTER NEWSROOM PROMPT
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. RESEARCH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Research the story using live web sources before writing.

Treat the supplied material as a lead.

Find the ORIGINAL / PRIMARY SOURCE whenever possible.

Prioritise:

• Government
• Police
• Courts
• Regulators
• Official organisations
• Official statements
• Direct statements
• Reputable Australian media

Use reputable secondary sources to confirm facts when necessary.

The final content must be based on information that can be verified from reliable sources.

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

Do not include information simply because it appears in the supplied article.

If an important claim cannot be verified, do not present it as fact.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. ORIGINAL ARTICLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write a completely ORIGINAL Australia By Aussie article.

Do not copy, translate or closely rewrite the source.

Use the verified facts and write the story independently.

The article must contain everything a reader genuinely needs to understand the story.

Include relevant:

• Names
• Numbers
• Dates
• Locations
• Important events
• Background
• Consequences
• Official responses
• Relevant statements
• What happens next

Do NOT add:

• Filler
• Generic introductions
• Repeated information
• Unnecessary background
• Speculation
• Unverified claims

Every paragraph must provide useful information.

Article length depends on the amount of important verified information.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3. QUOTES — IMPORTANT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use important VERIFIED direct quotes in:

• ARTICLE
• FACEBOOK POST
• VOICEOVER

Quotes must come from the original or reliable source.

Use the exact verified wording.

Clearly identify who said it.

Prioritise quotes that provide important information, reaction, confirmation or context.

Never invent, reconstruct or alter a quote.

If the exact wording cannot be verified, paraphrase it without quotation marks.

If there is an important verified quote, do not remove it unnecessarily.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4. VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Give:

VERIFICATION STATUS:
VERIFIED / PARTIALLY VERIFIED / DEVELOPING / INSUFFICIENT VERIFIED INFORMATION

Then briefly state:

CONFIRMED:
[Important confirmed facts]

NOT CONFIRMED:
[Only if something remains uncertain]

If the story is not sufficiently verified:

INSUFFICIENT VERIFIED INFORMATION — DO NOT PUBLISH

Never fill missing information with assumptions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5. CATEGORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use ONLY ONE of these categories:

• Australia
• Business
• Cost of Living
• Crime & Courts
• Explainers
• Life
• Politics
• World

There are NO subcategories.

Choose the category according to the CENTRAL SUBJECT of the story.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6. WEBSITE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HEADLINE

Create one factual headline.

Maximum 9 English words.

Arabic translation.

CATEGORY

[One approved category]

WHY:

[One short explanation]

EXCERPT

Write EXACTLY 25 ENGLISH WORDS.

Not 24.

Not 26.

Exactly 25 English words.

The excerpt must be based only on the verified article.

Then provide the Arabic translation.

TAG

Exactly ONE relevant WordPress tag.

IMAGE

Based on the actual image and article:

ALT TEXT
TITLE
CAPTION
DESCRIPTION

All must be factual.

Never invent what the image shows.

Provide Arabic translations.

ARTICLE

Write the complete original article using only verified information.

Include important names, numbers, dates, locations, statements, quotes, context and what happens next.

Then provide the complete Arabic translation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7. FACEBOOK / INSTAGRAM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create ONE detailed social post based directly on the article.

Maximum:

2,000 English characters INCLUDING spaces.

Target:

1,700–1,950 characters.

The post must be a condensed version of the article, NOT a teaser.

The reader should understand the actual story without opening the website.

Use the available space for important information.

Prioritise:

• Names
• Numbers
• Dates
• Locations
• Key events
• Important statements
• Important verified quotes
• Background needed to understand the story
• Why it matters
• What happens next

Do not add filler.

Do not waste characters on generic wording.

Include an important verified quote when one is available and relevant.

Use the exact verified wording.

End with:

👉 Have Your Say

Then ONE specific YES / NO question about the actual story.

The entire English post, including the question, MUST remain under 2,000 characters.

Then provide the complete Arabic translation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8. VIDEO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VIDEO TITLE

Factual title.

English + Arabic.

VOICEOVER

Write a complete news voiceover based only on the verified article.

Include the information the viewer needs to understand the story:

• Names
• Numbers
• Dates
• Locations
• Key facts
• Important statements
• Important verified quotes
• Why it matters
• What happens next

Use important verified quotes when available.

Use exact wording.

Clearly identify the speaker.

Do not add filler or unverified information.

Then provide the complete Arabic translation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
9. VIDEO CAPTION + HASHTAGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write one concise factual caption based on the article.

Immediately after it, write exactly three hashtags.

One MUST be:

#AustraliaByAussies

The other two must be relevant to the story.

Do not write labels such as:

Caption:
Hashtags:

Just write the caption followed by the three hashtags.

Then provide the Arabic caption.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
10. FINAL OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Always use this order:

🇦🇺 VERIFICATION

Status

Confirmed

Not Confirmed

---

📰 WEBSITE

Headline

Arabic Headline

Category

Why

25-Word Excerpt

Arabic Excerpt

One Tag

Alt Text

Arabic Alt Text

Title

Arabic Title

Caption

Arabic Caption

Description

Arabic Description

Complete English Article

Complete Arabic Article

---

📱 FACEBOOK / INSTAGRAM

Detailed English Post

👉 Have Your Say

YES / NO Question

Complete Arabic Post

👉 Have Your Say

Arabic Question

---

🎬 VIDEO

Video Title

Arabic Title

Complete Voiceover

Complete Arabic Voiceover

---

[Caption]

#AustraliaByAussies #RelevantHashtag #RelevantHashtag

Arabic Caption

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL RULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Give the reader the INFORMATION THEY ACTUALLY NEED.

Every important fact must come from a verified source.

Use names, numbers, dates, locations and important details.

Use verified direct quotes when available.

Write originally.

Remove anything that does not add useful information.

NO FILLER.
NO SPECULATION.
NO INVENTED FACTS.
NO INVENTED QUOTES.
NO COPIED ARTICLES.

EXCERPT = EXACTLY 25 ENGLISH WORDS.

FACEBOOK / INSTAGRAM = MAXIMUM 2,000 ENGLISH CHARACTERS.
"""


# ============================================================
# HARD EDITORIAL RULES
# ============================================================

EDITORIAL_RULES = r"""
NON-NEGOTIABLE EDITORIAL VERIFICATION RULES

1. SOURCE MATERIAL IS EVIDENCE, NOT TRUTH BY DEFAULT.

2. Every important factual claim must be supported by at least one
   reliable source.

3. High-risk claims should preferably have a primary/official source.

4. Distinguish clearly between:
   CONFIRMED
   REPORTED
   NOT CONFIRMED
   CONFLICTING

5. Never convert "reported", "alleged", "according to media",
   or "sources say" into confirmed fact.

6. If police, government, court, regulator, club or other official
   authority has not confirmed a claim, do not write that authority
   confirmed it.

7. A media report can establish that something was REPORTED,
   but does not automatically establish that the underlying allegation
   is true.

8. Never state that a person committed a crime unless that fact is
   officially established and legally safe to report.

9. For allegations involving sexual assault, abuse, serious crime,
   or misconduct, apply extra caution to names and alleged conduct.

10. If a person's name appears only in secondary reporting and their
    identity or involvement is not independently verified, do not
    present the person as confirmed to be involved.

11. Never invent quotes.

12. Never reconstruct a quote from a paraphrase.

13. A quote may only be used when its exact wording appears in the
    supplied source material.

14. If sources conflict on a material fact and the conflict cannot
    be resolved, mark the relevant information as NOT CONFIRMED.

15. If a central claim cannot be verified sufficiently:
    publish = false

16. Do not publish a developing story simply because it is trending.

17. Include important verified information even if it makes the article
    longer.

18. Do not remove important context merely to make the article shorter.

19. Do not add facts from model knowledge.

20. Use only information contained in the supplied evidence.

21. The article must be original in wording and structure.

22. Do not imitate the source article's paragraph structure.

23. Do not use source-specific phrasing unnecessarily.

24. The final article must distinguish allegations from established facts.

25. The verification decision must happen BEFORE final writing.
"""


# ============================================================
# STATE
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
        state.get(
            "processed",
            []
        )[-200:]
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


# ============================================================
# TEXT HELPERS
# ============================================================

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

    value = html.unescape(
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def article_id(url):

    return hashlib.sha256(
        url.encode(
            "utf-8"
        )
    ).hexdigest()[:20]


def count_words(text):

    return re.findall(
        r"\b[\w’'-]+\b",
        text,
        flags=re.UNICODE
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    timeout=30,
    headers=None
):

    default_headers = {
        "User-Agent":
            "AustraliaByAussie-Newsroom/2.0"
    }

    if headers:
        default_headers.update(
            headers
        )

    response = requests.get(
        url,
        timeout=timeout,
        headers=default_headers,
        allow_redirects=True
    )

    response.raise_for_status()

    return response


# ============================================================
# GUARDIAN
# ============================================================

def get_feed():

    response = http_get(
        GUARDIAN_RSS,
        timeout=30
    )

    return feedparser.parse(
        response.content
    )


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

        twitter_image = soup.find(
            "meta",
            attrs={
                "name": "twitter:image"
            }
        )

        if twitter_image:
            image_url = twitter_image.get(
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

    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "image_url": image_url,
        "text": "\n".join(
            paragraphs[:100]
        )
    }


# ============================================================
# GOOGLE NEWS RESEARCH
# ============================================================

def google_news_search(query):

    url = (
        GOOGLE_NEWS_RSS
        + "?q="
        + quote_plus(query)
        + "&hl=en-AU"
        + "&gl=AU"
        + "&ceid=AU:en"
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
            "Google News research error:",
            str(e)
        )

        return []

    results = []

    for entry in feed.entries[:12]:

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
# SOURCE PAGE EXTRACTION
# ============================================================

def fetch_source(url):

    try:

        response = http_get(
            url,
            timeout=30
        )

    except Exception as e:

        return {
            "url": url,
            "title": "",
            "description": "",
            "text": "",
            "error": str(e)
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

    return {
        "url": url,
        "title": clean_text(title),
        "description": clean_text(description),
        "text": "\n".join(
            paragraphs[:60]
        ),
        "error": ""
    }


# ============================================================
# SOURCE TYPE DETECTION
# ============================================================

def classify_source(url, title=""):

    value = (
        url
        + " "
        + title
    ).lower()

    official_domains = [
        ".gov.au",
        "police.nsw.gov.au",
        "police.vic.gov.au",
        "police.qld.gov.au",
        "police.wa.gov.au",
        "police.sa.gov.au",
        "police.tas.gov.au",
        "police.nt.gov.au",
        "afl.com.au",
        "sydneyswans.com.au",
        "abc.net.au",
        "court",
        "ato.gov.au",
        "treasury.gov.au",
        "pm.gov.au",
        "health.gov.au",
        "homeaffairs.gov.au",
        "asic.gov.au",
        "accc.gov.au"
    ]

    for domain in official_domains:

        if domain in value:

            return "PRIMARY_OR_OFFICIAL"

    reputable_domains = [
        "theguardian.com",
        "abc.net.au",
        "sbs.com.au",
        "smh.com.au",
        "theage.com.au",
        "news.com.au",
        "afr.com",
        "aap.com.au",
        "9news.com.au",
        "7news.com.au",
        "abc.net.au"
    ]

    for domain in reputable_domains:

        if domain in value:

            return "REPUTABLE_SECONDARY"

    return "SECONDARY_OR_UNKNOWN"


# ============================================================
# RESEARCH COLLECTION
# ============================================================

def collect_research(story):

    print(
        "Researching story before writing..."
    )

    searches = [
        story["title"],
        f'"{story["title"]}" Australia',
        f'{story["title"]} official statement',
    ]

    all_results = []

    seen_urls = set()

    for query in searches:

        results = google_news_search(
            query
        )

        for result in results:

            url = result["url"]

            if url in seen_urls:
                continue

            seen_urls.add(
                url
            )

            result["source_type"] = classify_source(
                url,
                result["title"]
            )

            all_results.append(
                result
            )

        time.sleep(0.3)

    # Primary/official first.
    all_results.sort(
        key=lambda item: (
            0
            if item["source_type"]
            == "PRIMARY_OR_OFFICIAL"
            else 1
            if item["source_type"]
            == "REPUTABLE_SECONDARY"
            else 2
        )
    )

    # Fetch a limited number of pages to stay light.
    pages = []

    for result in all_results[:10]:

        print(
            "Research source:",
            result["title"]
        )

        page = fetch_source(
            result["url"]
        )

        pages.append(
            {
                **result,
                "page_title": page["title"],
                "page_description": page["description"],
                "page_text": page["text"],
                "page_error": page["error"]
            }
        )

        time.sleep(0.2)

    return pages


def format_research(research):

    if not research:

        return (
            "NO ADDITIONAL SOURCES FOUND."
        )

    blocks = []

    for index, source in enumerate(
        research,
        start=1
    ):

        blocks.append(
            f"""
SOURCE {index}

SOURCE TYPE:
{source["source_type"]}

TITLE:
{source["title"]}

URL:
{source["url"]}

PUBLISHED:
{source["published"]}

SUMMARY:
{source["summary"]}

PAGE TITLE:
{source["page_title"]}

PAGE DESCRIPTION:
{source["page_description"]}

PAGE TEXT:
{source["page_text"][:9000]}
""".strip()
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# OPENROUTER
# ============================================================

def call_openrouter(
    messages,
    schema
):

    endpoint = (
        "https://openrouter.ai/api/v1/"
        "chat/completions"
    )

    payload = {
        "model": OPENROUTER_MODEL,

        "messages": messages,

        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name":
                    "australia_by_aussie_newsroom",
                "strict": True,
                "schema": schema
            }
        },

        "temperature": 0.15,

        "max_tokens": 18000,

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

    for attempt in range(
        1,
        4
    ):

        try:

            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=240
            )

            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                if attempt < 3:

                    wait = (
                        attempt * 6
                    )

                    print(
                        f"Temporary OpenRouter "
                        f"error {response.status_code}; "
                        f"retrying in {wait}s..."
                    )

                    time.sleep(
                        wait
                    )

                    continue

            response.raise_for_status()

            data = response.json()

            model_used = data.get(
                "model",
                "unknown"
            )

            print(
                "OpenRouter model used:",
                model_used
            )

            content = (
                data["choices"][0]
                ["message"]
                ["content"]
            )

            if not content:

                raise RuntimeError(
                    "OpenRouter returned empty content."
                )

            content = content.strip()

            if content.startswith("```"):

                content = re.sub(
                    r"^```json\s*",
                    "",
                    content,
                    flags=re.IGNORECASE
                )

                content = re.sub(
                    r"\s*```$",
                    "",
                    content
                )

                content = content.strip()

            return json.loads(
                content
            )

        except requests.exceptions.HTTPError as e:

            try:

                error = response.json()

            except Exception:

                error = response.text[:5000]

            raise RuntimeError(
                "OpenRouter HTTP error "
                f"{response.status_code}:\n"
                + json.dumps(
                    error,
                    ensure_ascii=False
                )[:6000]
            ) from e

        except requests.exceptions.RequestException as e:

            if attempt < 3:

                time.sleep(
                    attempt * 6
                )

                continue

            raise RuntimeError(
                f"OpenRouter request failed: {e}"
            ) from e


# ============================================================
# VERIFICATION SCHEMA
# ============================================================

VERIFICATION_SCHEMA = {

    "type": "object",

    "properties": {

        "status": {
            "type": "string",
            "enum": [
                "VERIFIED",
                "PARTIALLY VERIFIED",
                "DEVELOPING",
                "INSUFFICIENT VERIFIED INFORMATION"
            ]
        },

        "publish": {
            "type": "boolean"
        },

        "confirmed": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "reported_only": {
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
        },

        "conflicts": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "primary_sources": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "secondary_sources": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "verified_quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {
                        "type": "string"
                    },
                    "quote": {
                        "type": "string"
                    },
                    "source": {
                        "type": "string"
                    }
                },
                "required": [
                    "speaker",
                    "quote",
                    "source"
                ],
                "additionalProperties": False
            }
        }
    },

    "required": [
        "status",
        "publish",
        "confirmed",
        "reported_only",
        "not_confirmed",
        "conflicts",
        "primary_sources",
        "secondary_sources",
        "verified_quotes"
    ],

    "additionalProperties": False
}


# ============================================================
# VERIFICATION STAGE
# ============================================================

def verify_story(
    story,
    research
):

    research_text = format_research(
        research
    )

    prompt = f"""
You are the verification desk for Australia By Aussie.

Do NOT write an article.

Your ONLY job is to verify the supplied story.

Use the following MASTER NEWSROOM PRIORITY:

SOURCE ACCURACY
→ VERIFIED FACTS
→ PRIMARY SOURCES
→ RELIABLE SECONDARY CONFIRMATION
→ CLEAR UNCERTAINTY

{EDITORIAL_RULES}

IMPORTANT:

• The Guardian story is a lead, not automatic truth.
• Research sources are evidence.
• Primary/official sources have highest authority.
• Reputable Australian media can confirm reported information.
• Do not turn media reporting into official confirmation.
• Do not invent any facts.
• Do not invent quotes.
• Do not use model knowledge.
• If a central fact cannot be sufficiently verified, publish=false.

For each important factual claim, decide whether it is:

CONFIRMED
REPORTED_ONLY
NOT_CONFIRMED
CONFLICTING

A quote is verified ONLY if its exact wording appears in the supplied
source text.

SOURCE LEAD:

Title:
{story["title"]}

URL:
{story["url"]}

Description:
{story["description"]}

Guardian page text:
{story["text"]}

ADDITIONAL RESEARCH:

{research_text}

Return ONLY JSON.
"""

    result = call_openrouter(
        [
            {
                "role": "system",
                "content": (
                    "You are a strict senior news "
                    "verification editor."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        VERIFICATION_SCHEMA
    )

    return result


# ============================================================
# ARTICLE SCHEMA
# ============================================================

ARTICLE_SCHEMA = {

    "type": "object",

    "properties": {

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
                    "type": "string",
                    "enum": [
                        "Australia",
                        "Business",
                        "Cost of Living",
                        "Crime & Courts",
                        "Explainers",
                        "Life",
                        "Politics",
                        "World"
                    ]
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
        "website",
        "social",
        "video"
    ],

    "additionalProperties": False
}


# ============================================================
# WRITING STAGE
# ============================================================

def write_story(
    story,
    research,
    verification
):

    research_text = format_research(
        research
    )

    verification_text = json.dumps(
        verification,
        ensure_ascii=False,
        indent=2
    )

    prompt = f"""
{MASTER_PROMPT}

{EDITORIAL_RULES}

AUTOMATED WORKFLOW RULES

The verification stage has already been completed.

You MUST write ONLY from the VERIFIED EVIDENCE below.

Do not add information from model memory.

Do not invent anything.

Do not upgrade reported information into confirmed information.

If the verification says something is reported only,
preserve that distinction in the article.

If a fact is not confirmed, do not state it as established fact.

IMPORTANT QUOTE RULE:

You may use ONLY quotes listed in verified_quotes.

Use the exact wording.

Do not alter them.

Do not create new quotes.

IMPORTANT COMPLETENESS RULE:

Do not unnecessarily shorten the story.

Include every important verified fact needed for a reader
to understand the story.

Do not add filler.

SOURCE LEAD:

Title:
{story["title"]}

URL:
{story["url"]}

Guardian page text:
{story["text"]}

VERIFICATION REPORT:

{verification_text}

RESEARCH EVIDENCE:

{research_text}

Return ONLY the JSON object matching the required schema.
"""

    return call_openrouter(
        [
            {
                "role": "system",
                "content": (
                    "You are the senior journalist and "
                    "fact-checked news editor for "
                    "Australia By Aussie."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        ARTICLE_SCHEMA
    )


# ============================================================
# HARD VALIDATION
# ============================================================

def validate_output(
    result,
    verification
):

    errors = []

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

    facebook = social.get(
        "english",
        ""
    )

    hashtags = video.get(
        "hashtags",
        []
    )

    article_html = website.get(
        "article_html",
        ""
    )

    arabic_article = website.get(
        "arabic_article_html",
        ""
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

    # --------------------------------------------------------
    # VERIFICATION GATE
    # --------------------------------------------------------

    status = verification.get(
        "status",
        ""
    )

    if status not in {
        "VERIFIED",
        "PARTIALLY VERIFIED",
        "DEVELOPING"
    }:

        errors.append(
            "Verification status is insufficient."
        )

    if not verification.get(
        "publish",
        False
    ):

        errors.append(
            "Verification stage rejected publication."
        )

    if verification.get(
        "conflicts"
    ):

        errors.append(
            "Material source conflicts remain."
        )

    # --------------------------------------------------------
    # HEADLINE
    # --------------------------------------------------------

    if len(
        headline.split()
    ) > 9:

        errors.append(
            "Headline exceeds 9 English words."
        )

    # --------------------------------------------------------
    # EXCERPT
    # --------------------------------------------------------

    excerpt_words = count_words(
        excerpt
    )

    if len(excerpt_words) != 25:

        errors.append(
            "Excerpt must contain exactly "
            f"25 English words; found "
            f"{len(excerpt_words)}."
        )

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

    if website.get(
        "category"
    ) not in allowed_categories:

        errors.append(
            "Invalid category."
        )

    # --------------------------------------------------------
    # TAG
    # --------------------------------------------------------

    if not tag:

        errors.append(
            "Missing WordPress tag."
        )

    # --------------------------------------------------------
    # ARTICLE
    # --------------------------------------------------------

    if len(
        BeautifulSoup(
            article_html,
            "html.parser"
        ).get_text(
            " ",
            strip=True
        )
    ) < 250:

        errors.append(
            "English article is too short."
        )

    if len(
        BeautifulSoup(
            arabic_article,
            "html.parser"
        ).get_text(
            " ",
            strip=True
        )
    ) < 150:

        errors.append(
            "Arabic article is too short."
        )

    # --------------------------------------------------------
    # FACEBOOK
    # --------------------------------------------------------

    if len(facebook) > 2000:

        errors.append(
            "Facebook post exceeds 2,000 characters."
        )

    if "👉 Have Your Say" not in facebook:

        errors.append(
            "Facebook post missing Have Your Say."
        )

    # --------------------------------------------------------
    # HASHTAGS
    # --------------------------------------------------------

    if len(hashtags) != 3:

        errors.append(
            "Exactly three hashtags required."
        )

    if "#AustraliaByAussies" not in hashtags:

        errors.append(
            "Missing #AustraliaByAussies."
        )

    # --------------------------------------------------------
    # IMAGE METADATA
    # --------------------------------------------------------

    for field in [
        "alt_text",
        "image_title",
        "caption",
        "description"
    ]:

        if not website.get(field):

            errors.append(
                f"Missing image field: {field}"
            )

    if errors:

        raise ValueError(
            "FINAL VALIDATION FAILED:\n"
            + "\n".join(
                f"- {error}"
                for error in errors
            )
        )


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

    endpoint = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    upload = requests.post(
        endpoint,
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

    metadata = requests.post(
        f"{endpoint}/{media_id}",
        auth=wp_auth(),
        json={
            "alt_text": alt_text
        },
        timeout=30
    )

    metadata.raise_for_status()

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
            "title":
                content["headline"],

            "content":
                content["article_html"],

            "excerpt":
                content["excerpt"],

            "status":
                "publish",

            "featured_media":
                featured_media
        },
        timeout=90
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=============================================="
    )

    print(
        "Australia By Aussie Automated Newsroom V2"
    )

    print(
        "PROFESSIONAL VERIFICATION EDITION"
    )

    print(
        "=============================================="
    )

    state = load_state()

    processed = set(
        state.get(
            "processed",
            []
        )
    )

    # --------------------------------------------------------
    # FIND NEWS
    # --------------------------------------------------------

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

        url = (
            entry.get(
                "link",
                ""
            )
            .strip()
        )

        if not url:
            continue

        key = article_id(
            url
        )

        if key in processed:
            continue

        candidates.append(
            {
                "id":
                    key,

                "url":
                    url,

                "title":
                    clean_text(
                        entry.get(
                            "title",
                            ""
                        )
                    ),

                "description":
                    clean_text(
                        entry.get(
                            "summary",
                            ""
                        )
                    )
            }
        )

    if not candidates:

        print(
            "No new stories."
        )

        return

    story = candidates[0]

    print(
        f"Found {len(candidates)} new candidates."
    )

    print(
        "Selected:"
    )

    print(
        story["title"]
    )

    # --------------------------------------------------------
    # SOURCE PAGE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    research = collect_research(
        story
    )

    print(
        f"Collected {len(research)} research sources."
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    print(
        "=============================================="
    )

    print(
        "STARTING VERIFICATION BEFORE WRITING"
    )

    print(
        "=============================================="
    )

    verification = verify_story(
        story,
        research
    )

    print(
        "Verification status:",
        verification.get(
            "status"
        )
    )

    print(
        "Confirmed facts:",
        len(
            verification.get(
                "confirmed",
                []
            )
        )
    )

    print(
        "Reported-only facts:",
        len(
            verification.get(
                "reported_only",
                []
            )
        )
    )

    print(
        "Not confirmed:",
        len(
            verification.get(
                "not_confirmed",
                []
            )
        )
    )

    print(
        "Primary sources:",
        len(
            verification.get(
                "primary_sources",
                []
            )
        )
    )

    print(
        "Verified quotes:",
        len(
            verification.get(
                "verified_quotes",
                []
            )
        )
    )

    # --------------------------------------------------------
    # HARD STOP BEFORE WRITING
    # --------------------------------------------------------

    if not verification.get(
        "publish",
        False
    ):

        print(
            "=============================================="
        )

        print(
            "DO NOT PUBLISH"
        )

        print(
            "Verification did not approve publication."
        )

        print(
            "=============================================="
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

    if verification.get(
        "conflicts"
    ):

        print(
            "Material source conflicts detected."
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
    # WRITING
    # --------------------------------------------------------

    print(
        "Verification passed."
    )

    print(
        "Writing original Australia By Aussie story..."
    )

    result = write_story(
        story,
        research,
        verification
    )

    # --------------------------------------------------------
    # FINAL VALIDATION
    # --------------------------------------------------------

    print(
        "Running final editorial validation..."
    )

    validate_output(
        result,
        verification
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

        head = requests.head(
            story["image_url"],
            timeout=30,
            headers={
                "User-Agent":
                    "AustraliaByAussie-Newsroom/2.0"
            },
            allow_redirects=True
        )

        content_type = head.headers.get(
            "Content-Type",
            ""
        ).lower()

        if "png" in content_type:

            extension = ".png"

        elif "webp" in content_type:

            extension = ".webp"

    except Exception:

        pass

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

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

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
    # STATE
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
