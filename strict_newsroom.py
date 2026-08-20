"""Strict entrypoint for the Australia By Aussie newsroom.

The legacy scraper can read every <p> on a Guardian page. This wrapper blocks
Guardian live blogs and roundup/newsletter pages before they can reach the AI,
which prevents multiple unrelated stories being merged into one article.
"""
import re

import newsroom_router as router


ROUNDUP_TITLE_WORDS = (
    "afternoon update",
    "morning mail",
    "morning update",
    "evening update",
    "weekly update",
    "weekend read",
    "newsletter",
    "as it happened",
    "live updates",
    "live blog",
)


def is_standalone_guardian_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if not value.startswith("https://www.theguardian.com/australia-news/"):
        return False
    if "/australia-news/live/" in value:
        return False
    if any(token in value for token in ("/live/", "-live-", "-as-it-happened", "afternoon-update", "morning-mail")):
        return False
    # Standalone Guardian article URLs use a dated /YYYY/mon/day/ path.
    if not re.search(r"/australia-news/\d{4}/[a-z]{3}/\d{2}/", value):
        return False
    return True


_original_feed_candidates = router.feed_candidates
_original_get_article_page = router.base.get_article_page


def strict_feed_candidates(url, source):
    items = _original_feed_candidates(url, source)
    clean = []
    for item in items:
        title = str(item.get("title", "")).strip().lower()
        if not is_standalone_guardian_url(item.get("url", "")):
            print("SOURCE REJECTED: non-standalone Guardian URL:", item.get("url", ""))
            continue
        if any(word in title for word in ROUNDUP_TITLE_WORDS):
            print("SOURCE REJECTED: Guardian roundup/live title:", item.get("title", ""))
            continue
        clean.append(item)
    return clean


def strict_get_article_page(url):
    if not is_standalone_guardian_url(url):
        raise RuntimeError("NO_PUBLICATION: Guardian source is not a standalone article; live/roundup pages are blocked.")
    page = _original_get_article_page(url)
    page_title = str(page.get("title", "")).strip().lower()
    if any(word in page_title for word in ROUNDUP_TITLE_WORDS):
        raise RuntimeError("NO_PUBLICATION: Guardian page is a roundup/live/newsletter page.")
    if not page.get("text"):
        raise RuntimeError("NO_PUBLICATION: Guardian standalone article has no article text.")
    return page


router.feed_candidates = strict_feed_candidates
router.base.get_article_page = strict_get_article_page


if __name__ == "__main__":
    router.run()
