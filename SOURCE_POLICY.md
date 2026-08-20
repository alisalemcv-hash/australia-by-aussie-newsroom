# Source policy

The newsroom may publish only standalone Guardian Australia articles.

Rejected source pages:
- Guardian live blogs (`/australia-news/live/`)
- Morning Mail / Afternoon Update roundups
- live/rolling coverage and other multi-story roundup pages
- non-Guardian URLs

A Guardian Australia RSS entry is only a lead. The fetched page must be a standalone article and the article-body extractor must use only the main article body, never the entire page's `<p>` elements. If a compliant standalone article cannot be verified, publication must be blocked.
