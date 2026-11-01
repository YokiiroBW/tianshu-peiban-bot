"""Readable bodies with explicit forum speakers, quoted speakers and page identity."""

from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup


def quoted_text(node):
    clone = BeautifulSoup(str(node), "html.parser")
    for item in clone.select("cite,.title,.quote-title,.quote-author,.quote-controls"):
        item.decompose()
    return clone.get_text("\n", strip=True)


def post_body(node):
    quotes = []
    for quote in node.select("blockquote,aside.quote"):
        if any(parent.name == "blockquote" or "quote" in (parent.get("class") or [])
               for parent in quote.parents if parent is not node):
            continue
        cite = quote.select_one("cite,.quote-title,.quote-author,a[data-user-card]")
        link = quote.select_one("a[href]")
        quotes.append({"author": quote.get("data-username") or quote.get("data-author")
                       or (cite.get_text(" ", strip=True) if cite else None),
                       "post_id": quote.get("data-post-id"),
                       "floor": quote.get("data-post") or quote.get("data-post-number"),
                       "url": link.get("href") if link else None,
                       "text": quoted_text(quote)})
    clone = BeautifulSoup(str(node), "html.parser")
    for item in clone.select("blockquote,aside.quote,cite,.signature,.post-actions,.reactions"):
        item.decompose()
    return clone.get_text("\n", strip=True), quotes


def post_text(post):
    lines = [f"#{post['floor']} author={post.get('author') or 'unknown'}", post["text"]]
    for quote in post.get("quotes", []):
        lines.extend([f"Quoted author={quote.get('author') or 'unknown'} "
                      f"floor={quote.get('floor') or 'unknown'} (quotation, not current speaker):",
                      quote["text"]])
    return "\n".join(lines)


def html_content(raw, url):
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    for node in soup.select("script,style,noscript,nav,footer,form,.advertisement"):
        node.decompose()
    posts = []
    nodes = soup.select("[data-post-id],article.topic-post,.forum-post,.message[id^=post-]")
    for node in nodes:
        if any(parent in nodes for parent in node.parents):
            continue
        author = node.select_one("[data-user-card],.username,.author,[itemprop=author]")
        body = node.select_one(".cooked,.post-content,.message-body,[itemprop=text]") or node
        text, quotes = post_body(body)
        for quote in quotes:
            if quote.get("url"):
                quote["url"] = urljoin(url, quote["url"])
        posted = node.select_one("time,[itemprop=datePublished]")
        posts.append({"id": str(node.get("data-post-id") or node.get("id") or len(posts) + 1),
                      "floor": node.get("data-post-number", len(posts) + 1),
                      "author": author.get_text(" ", strip=True) if author else node.get("data-author"),
                      "text": text, "quotes": quotes,
                      "reply_to_floor": node.get("data-reply-to-post-number"),
                      "created_at": (posted.get("datetime") or posted.get("content")) if posted else None,
                      "edited_at": node.get("data-edited-at"),
                      "revision": node.get("data-version") or node.get("data-revision")})
    next_link = soup.select_one("a[rel~=next],.pagination a.next,a.next-page")
    canonical = soup.select_one("link[rel=canonical]")
    published = soup.select_one("meta[property='article:published_time'],meta[name='date']")
    author = soup.select_one("meta[name=author]")
    main = soup.select_one("main article,article,main,[role=main],.article-content,.post-content") or soup.body or soup
    return {"title": title, "text": "\n\n".join(map(post_text, posts)) if posts else main.get_text("\n", strip=True),
            "posts": posts, "authors": list(dict.fromkeys(p["author"] for p in posts if p["author"]))
                    if posts else [author.get("content")] if author else [],
            "published_at": published.get("content") if published else None,
            "canonical_url": urljoin(url, canonical["href"]) if canonical and canonical.get("href") else url,
            "next_url": urljoin(url, next_link["href"]) if next_link and next_link.get("href") else None,
            "forum_cursor": None}


def discourse(data, url, cursor=None):
    cursor = cursor or {}
    posts = []
    for value in data["post_stream"].get("posts", []):
        node = BeautifulSoup(value.get("cooked", ""), "html.parser")
        text, quotes = post_body(node)
        for quote in quotes:
            if quote.get("url"):
                quote["url"] = urljoin(url, quote["url"])
        posts.append({"id": str(value["id"]), "floor": value["post_number"],
                      "author": value.get("username"), "text": text, "quotes": quotes,
                      "reply_to_floor": value.get("reply_to_post_number"),
                      "created_at": value.get("created_at"), "edited_at": value.get("updated_at"),
                      "revision": value.get("version")})
    loaded = {str(post["id"]) for post in posts}
    stream = data["post_stream"].get("stream") or cursor.get("remaining_post_ids", [])
    remaining = [str(item) for item in stream if str(item) not in loaded]
    topic_id = data.get("id") or cursor.get("topic_id")
    topic_url = cursor.get("topic_url") or (urljoin(url, f"/t/{topic_id}.json") if topic_id else url)
    title = data.get("title") or cursor.get("title")
    return {"title": title, "text": "\n\n".join(map(post_text, posts)), "posts": posts,
            "authors": list(dict.fromkeys(post["author"] for post in posts if post["author"])),
            "published_at": data.get("created_at"), "canonical_url": topic_url,
            "next_url": urljoin(url, f"/t/{topic_id}/posts.json?") + urlencode(
                [("post_ids[]", item) for item in remaining[:20]]) if remaining and topic_id else None,
            "forum_cursor": {"topic_id": topic_id, "topic_url": topic_url,
                             "title": title, "remaining_post_ids": remaining} if remaining else None}
