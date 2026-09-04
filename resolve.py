"""Turn raw ingredient lines into structured rows, then load them.

    python3 resolve.py inbox/weeknight-chickpea-stew.json           # review only
    python3 resolve.py inbox/weeknight-chickpea-stew.json --commit  # write to db

Parsing is the one job worth handing to a model: "1 (15 oz) can chickpeas,
drained" has to become quantity/unit/name/prep, and regexes lose that fight.
Everything after the parse is deterministic lookup against your own tables.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import requests

DB = Path(__file__).parent / "recipes.db"
MODEL = "claude-sonnet-4-6"

SYSTEM = """You parse recipe ingredient lines into structured data.

Return ONLY a JSON array, no prose and no markdown fences. One object per
input line, in the same order, with these keys:

  quantity   number, or null for "to taste" / "as needed"
  unit       one of: tsp tbsp cup "fl oz" ml l pint quart g kg oz lb
             whole clove bunch can pinch
             Use null when the line has a bare number and no unit.
  name       the ingredient in its plainest singular form. Strip brand names,
             preparation words, and adjectives that describe cutting or state.
             "freshly minced garlic" -> "garlic". "large eggs" -> "egg".
             Keep adjectives that change what you buy: "smoked paprika",
             "all-purpose flour", "heavy cream" stay intact.
  prep_note  minced, chopped, drained, divided, room temperature, etc. Null if none.
  optional   true if the line is marked optional or "if desired".

For canned goods prefer the package unit: "1 (15 oz) can chickpeas, drained"
-> quantity 1, unit "can", name "chickpeas", prep_note "drained".
"""


def parse_lines(lines):
    """Send every line in one request; the model sees the whole recipe at once."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY.")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 2000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": "\n".join(lines)}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = "".join(b.get("text", "") for b in resp.json()["content"])
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(text)


def lookup(conn, name):
    """Canonical name -> ingredient id, via the ingredients table then aliases."""
    row = conn.execute(
        "SELECT id FROM ingredients WHERE canonical_name = ?", (name,)
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT ingredient_id FROM ingredient_aliases WHERE alias = ?", (name,)
    ).fetchone()
    return row[0] if row else None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    commit = "--commit" in sys.argv
    if not args:
        sys.exit(__doc__)

    path = Path(args[0])
    recipe = json.loads(path.read_text())
    lines = recipe["ingredient_lines"]

    # The parse is cached back into the inbox file so you can correct it by
    # hand. Only the first run costs an API call; --reparse forces a new one.
    if recipe.get("parsed") and "--reparse" not in sys.argv:
        parsed = recipe["parsed"]
        source = "cached"
    else:
        parsed = parse_lines(lines)
        recipe["parsed"] = parsed
        path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False))
        source = "model"

    if len(parsed) != len(lines):
        print(f"Parser returned {len(parsed)} rows for {len(lines)} lines.\n")
        # Walk both lists together. The first row whose ingredient name is
        # absent from its matching input line is where alignment broke.
        for i in range(max(len(lines), len(parsed))):
            line = lines[i] if i < len(lines) else ""
            p = parsed[i] if i < len(parsed) else {}
            name = (p.get("name") or "")
            words = [w for w in name.lower().split() if len(w) > 3]
            ok = not line or not name or any(w in line.lower() for w in words) \
                 or (not words and name.lower() in line.lower())
            print(f"  {'  ' if ok else '>>'} {i:>2}  {line[:44]:<44} | {name}")
        print(f"\nFix the \"parsed\" array in {path} — merge the split row or "
              "delete the extra — then rerun.")
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")

    valid_units = {r[0] for r in conn.execute("SELECT name FROM units")}
    rows, new_ingredients, problems = [], [], []

    for raw, p in zip(lines, parsed):
        unit = p.get("unit")
        if unit and unit not in valid_units:
            problems.append(f"unknown unit {unit!r} in: {raw}")
            unit = None
        iid = lookup(conn, p["name"])
        if iid is None:
            new_ingredients.append(p["name"])
        rows.append((iid, p["name"], p.get("quantity"), unit,
                     p.get("prep_note"), int(bool(p.get("optional"))), raw))

    print(f"\n{recipe['title']}  (serves {recipe['servings']})\n")
    for iid, name, qty, unit, prep, opt, raw in rows:
        mark = " " if iid else "+"
        amount = " ".join(str(x) for x in (qty, unit) if x is not None) or "to taste"
        print(f" {mark} {amount:<12} {name:<24} {prep or ''}")
    if new_ingredients:
        print(f"\n  + {len(new_ingredients)} new to the ingredients table")
    for p in problems:
        print(f"  ! {p}")

    if not commit:
        print(f"\nParse source: {source}.")
        print(f"Wrong? Edit the \"parsed\" array in {path}, then rerun.")
        print("Rerun with --commit when it looks right.")
        return

    for name in new_ingredients:
        conn.execute("INSERT INTO ingredients (canonical_name) VALUES (?)", (name,))
    conn.commit()

    cur = conn.execute(
        "INSERT INTO recipes (title, source_url, servings, instructions, notes) "
        "VALUES (?,?,?,?,?)",
        (recipe["title"], recipe.get("source_url"), recipe.get("servings"),
         recipe.get("instructions"), recipe.get("notes")),
    )
    rid = cur.lastrowid

    for _, name, qty, unit, prep, opt, raw in rows:
        iid = lookup(conn, name)
        conn.execute(
            "INSERT INTO recipe_ingredients "
            "(recipe_id, ingredient_id, quantity, unit, prep_note, optional, raw_text) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, iid, qty, unit, prep, opt, raw),
        )
    conn.commit()
    print(f"\nCommitted as recipe {rid}.")


if __name__ == "__main__":
    main()
