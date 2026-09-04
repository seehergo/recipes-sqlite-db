"""Point one ingredient name at another.

    python3 alias.py scallion "green onion"

Creates the alias so future recipes resolve correctly. If "scallion" already
exists as its own ingredient row, its recipe references are moved onto
"green onion" and the duplicate row is removed.

    python3 alias.py --list                     show every alias
    python3 alias.py --delete scallion          remove an alias
    python3 alias.py scallion onion --retarget  repoint an existing alias
"""

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "recipes.db"


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")

    if "--list" in sys.argv:
        rows = conn.execute(
            "SELECT a.alias, i.canonical_name FROM ingredient_aliases a "
            "JOIN ingredients i ON i.id = a.ingredient_id ORDER BY i.canonical_name"
        ).fetchall()
        for alias, canon in rows:
            print(f"  {alias:<28} -> {canon}")
        print(f"\n{len(rows)} aliases.")
        return

    if "--delete" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        if len(args) != 1:
            sys.exit("Usage: python3 alias.py --delete <alias>")
        n = conn.execute(
            "DELETE FROM ingredient_aliases WHERE alias = ?", (args[0],)
        ).rowcount
        conn.commit()
        if not n:
            sys.exit(f"No alias named {args[0]!r}.")
        print(f"Deleted alias {args[0]!r}.")
        print("Note: any recipe rows already merged under the canonical name "
              "stay there. This only affects future parses.")
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    losing, keeping = args

    row = conn.execute(
        "SELECT id FROM ingredients WHERE canonical_name = ?", (keeping,)
    ).fetchone()
    if not row:
        sys.exit(f"No ingredient named {keeping!r}. Check spelling.")
    keep_id = row[0]

    if losing.lower() == keeping.lower():
        sys.exit("Those are the same name.")

    existing = conn.execute(
        "SELECT ingredient_id FROM ingredient_aliases WHERE alias = ?", (losing,)
    ).fetchone()
    if existing:
        if existing[0] == keep_id:
            sys.exit(f"{losing!r} already points at {keeping!r}.")
        if "--retarget" not in sys.argv:
            old = conn.execute(
                "SELECT canonical_name FROM ingredients WHERE id = ?", (existing[0],)
            ).fetchone()[0]
            sys.exit(f"{losing!r} currently points at {old!r}. "
                     f"Rerun with --retarget to repoint it at {keeping!r}.")
        conn.execute(
            "UPDATE ingredient_aliases SET ingredient_id = ? WHERE alias = ?",
            (keep_id, losing),
        )
        conn.commit()
        print(f"Repointed {losing!r} -> {keeping!r}")
        print("Note: recipe rows merged under the old target are not moved back.")
        return

    # If the losing name exists as a real ingredient, absorb it.
    dup = conn.execute(
        "SELECT id FROM ingredients WHERE canonical_name = ?", (losing,)
    ).fetchone()
    moved = 0
    if dup:
        moved = conn.execute(
            "UPDATE recipe_ingredients SET ingredient_id = ? WHERE ingredient_id = ?",
            (keep_id, dup[0]),
        ).rowcount
        conn.execute("DELETE FROM ingredients WHERE id = ?", (dup[0],))

    conn.execute(
        "INSERT INTO ingredient_aliases (alias, ingredient_id) VALUES (?, ?)",
        (losing, keep_id),
    )
    conn.commit()

    print(f"{losing!r} -> {keeping!r}")
    if dup:
        print(f"  merged duplicate row, moved {moved} recipe reference(s)")


if __name__ == "__main__":
    main()
