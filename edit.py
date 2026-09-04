"""Change a recipe that's already in the database.

    python3 edit.py --list
    python3 edit.py 3 show
    python3 edit.py 3 set garlic 5 clove
    python3 edit.py 3 drop "heavy cream"
    python3 edit.py 3 add "smoked paprika" 1 tsp --prep "toasted"
    python3 edit.py 3 note "Cut the cream, added a squeeze of lemon at the end."
    python3 edit.py 3 fork "Clam Chowder (my version)"

set/drop/add mutate the recipe in place — use them to correct bad data.
fork copies the recipe and its ingredients into a new row linked back to the
original, so you can change the copy and keep both.
"""

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "recipes.db"

MIGRATION = """
ALTER TABLE recipes ADD COLUMN variant_of INTEGER REFERENCES recipes(id);
"""


def migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(recipes)")}
    if "variant_of" not in cols:
        conn.executescript(MIGRATION)
        conn.commit()


def lookup(conn, name):
    row = conn.execute(
        "SELECT id FROM ingredients WHERE canonical_name = ?", (name,)
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT ingredient_id FROM ingredient_aliases WHERE alias = ?", (name,)
    ).fetchone()
    return row[0] if row else None


def show(conn, rid):
    r = conn.execute(
        "SELECT title, servings, variant_of, notes FROM recipes WHERE id = ?", (rid,)
    ).fetchone()
    if not r:
        sys.exit(f"No recipe {rid}.")
    title, servings, variant_of, notes = r
    line = f"\n[{rid}] {title}  (serves {servings})"
    if variant_of:
        parent = conn.execute(
            "SELECT title FROM recipes WHERE id = ?", (variant_of,)
        ).fetchone()[0]
        line += f"   variant of [{variant_of}] {parent}"
    print(line + "\n")
    for name, qty, unit, prep, raw in conn.execute(
        "SELECT i.canonical_name, ri.quantity, ri.unit, ri.prep_note, ri.raw_text "
        "FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id "
        "WHERE ri.recipe_id = ? ORDER BY i.canonical_name", (rid,)
    ):
        amount = " ".join(str(x) for x in (qty, unit) if x is not None) or "to taste"
        flag = "" if raw else "  *edited"
        print(f"  {amount:<12} {name:<24} {prep or '':<16}{flag}")
    if notes:
        print(f"\n  {notes}")


def main():
    if "--list" in sys.argv:
        conn = sqlite3.connect(DB)
        migrate(conn)
        for rid, title, n, parent in conn.execute(
            "SELECT r.id, r.title, COUNT(ri.id), r.variant_of "
            "FROM recipes r LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id "
            "GROUP BY r.id ORDER BY r.title"
        ):
            mark = f"  (variant of {parent})" if parent else ""
            print(f"  [{rid:>3}] {title:<38} {n} ingredients{mark}")
        return

    if len(sys.argv) < 3:
        sys.exit(__doc__)
    rid, cmd = int(sys.argv[1]), sys.argv[2]
    rest = [a for a in sys.argv[3:] if not a.startswith("--")]

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)

    if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (rid,)).fetchone():
        sys.exit(f"No recipe {rid}.")

    if cmd == "show":
        show(conn, rid)
        return

    if cmd == "note":
        conn.execute("UPDATE recipes SET notes = ? WHERE id = ?", (rest[0], rid))
        conn.commit()
        print("Note updated.")
        return

    if cmd == "fork":
        title = rest[0]
        cur = conn.execute(
            "INSERT INTO recipes (title, source_url, servings, instructions, notes, variant_of) "
            "SELECT ?, source_url, servings, instructions, notes, ? FROM recipes WHERE id = ?",
            (title, rid, rid),
        )
        new = cur.lastrowid
        conn.execute(
            "INSERT INTO recipe_ingredients "
            "(recipe_id, ingredient_id, quantity, unit, prep_note, optional, raw_text) "
            "SELECT ?, ingredient_id, quantity, unit, prep_note, optional, raw_text "
            "FROM recipe_ingredients WHERE recipe_id = ?",
            (new, rid),
        )
        conn.commit()
        print(f"Forked to recipe {new}. Edit that one.")
        show(conn, new)
        return

    # set / drop / add all target one ingredient
    if not rest:
        sys.exit(__doc__)
    name = rest[0]
    iid = lookup(conn, name)

    if cmd == "drop":
        if iid is None:
            sys.exit(f"No ingredient named {name!r}.")
        n = conn.execute(
            "DELETE FROM recipe_ingredients WHERE recipe_id = ? AND ingredient_id = ?",
            (rid, iid),
        ).rowcount
        conn.commit()
        print(f"Removed {name!r} ({n} row).") if n else sys.exit(f"{name!r} not in recipe {rid}.")

    elif cmd in ("set", "add"):
        qty = float(rest[1]) if len(rest) > 1 and rest[1].lower() != "none" else None
        unit = rest[2] if len(rest) > 2 else None
        prep = None
        if "--prep" in sys.argv:
            prep = sys.argv[sys.argv.index("--prep") + 1]

        if unit and not conn.execute(
            "SELECT 1 FROM units WHERE name = ?", (unit,)
        ).fetchone():
            sys.exit(f"Unknown unit {unit!r}. See the units table.")

        if iid is None:
            if cmd == "set":
                sys.exit(f"No ingredient named {name!r}. Use 'add' to create it.")
            conn.execute("INSERT INTO ingredients (canonical_name) VALUES (?)", (name,))
            iid = lookup(conn, name)
            print(f"Created ingredient {name!r}.")

        existing = conn.execute(
            "SELECT id FROM recipe_ingredients WHERE recipe_id = ? AND ingredient_id = ?",
            (rid, iid),
        ).fetchone()

        if existing:
            # raw_text cleared: this row no longer reflects the original source
            conn.execute(
                "UPDATE recipe_ingredients SET quantity = ?, unit = ?, "
                "prep_note = COALESCE(?, prep_note), raw_text = NULL WHERE id = ?",
                (qty, unit, prep, existing[0]),
            )
            print(f"Updated {name!r}.")
        else:
            conn.execute(
                "INSERT INTO recipe_ingredients "
                "(recipe_id, ingredient_id, quantity, unit, prep_note) VALUES (?,?,?,?,?)",
                (rid, iid, qty, unit, prep),
            )
            print(f"Added {name!r}.")
        conn.commit()

    else:
        sys.exit(__doc__)

    show(conn, rid)


if __name__ == "__main__":
    main()
