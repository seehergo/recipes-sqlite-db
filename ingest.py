"""Pull structured recipe data out of a web page's schema.org JSON-LD.

No LLM involved. Most recipe sites embed the full recipe as machine-readable
JSON already; this just finds it and flattens the shape variations.

    python3 ingest.py https://example.com/some-recipe
    python3 ingest.py --file saved_page.html
"""

import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

INBOX = Path(__file__).parent / "inbox"
HEADERS = {"User-Agent": "Mozilla/5.0 (recipe-collector)"}


def _iter_jsonld(soup):
    """Yield every JSON-LD object on the page, flattening lists and @graph."""
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Some sites emit trailing commas or stray control characters.
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node


def _is_recipe(node):
    t = node.get("@type", "")
    return "Recipe" in (t if isinstance(t, list) else [t])


def _text(value):
    """schema.org fields arrive as str, dict, or list of either."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("name"))
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        return "\n".join(p for p in parts if p)
    return str(value)


def _instructions(value):
    """Flatten HowToStep / HowToSection nesting into numbered lines."""
    steps = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            if node.get("@type") == "HowToSection":
                walk(node.get("itemListElement", []))
            else:
                t = _text(node)
                if t:
                    steps.append(t)
        elif isinstance(node, str):
            # A single blob of prose; split on sentence-ish boundaries.
            for line in re.split(r"\n+", node):
                line = line.strip()
                if line:
                    steps.append(line)

    walk(value)
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def _servings(value):
    t = _text(value)
    if not t:
        return None
    m = re.search(r"\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None


def extract(html, source_url=None):
    soup = BeautifulSoup(html, "lxml")
    for node in _iter_jsonld(soup):
        if not _is_recipe(node):
            continue
        ingredients = node.get("recipeIngredient") or node.get("ingredients") or []
        if isinstance(ingredients, str):
            ingredients = [ingredients]
        return {
            "title": _text(node.get("name")),
            "source_url": source_url or _text(node.get("url")),
            "servings": _servings(node.get("recipeYield")),
            "ingredient_lines": [i.strip() for i in ingredients if i and i.strip()],
            "instructions": _instructions(node.get("recipeInstructions")),
            "notes": _text(node.get("description")),
        }
    return None


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return extract(resp.text, source_url=url)


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")[:60]


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)

    if args[0] == "--file":
        html = Path(args[1]).read_text(encoding="utf-8", errors="replace")
        recipe = extract(html)
    else:
        recipe = fetch(args[0])

    if not recipe:
        sys.exit("No schema.org Recipe found. Fall back to manual or vision entry.")

    INBOX.mkdir(exist_ok=True)
    out = INBOX / f"{slugify(recipe['title'])}.json"
    out.write_text(json.dumps(recipe, indent=2, ensure_ascii=False))
    print(f"{recipe['title']} -> {out}")
    print(f"  {len(recipe['ingredient_lines'])} ingredient lines, "
          f"serves {recipe['servings']}")


if __name__ == "__main__":
    main()
