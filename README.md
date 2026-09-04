# Recipe Manager

A personal recipe database with a natural-language front end. Recipes are stored
as structured rows in SQLite, so shopping lists, ingredient searches, and
quantity merging are ordinary SQL rather than something a language model has to
get right every time.

A model is used in exactly one place: turning `1 (15 oz) can chickpeas, drained`
into `{quantity: 1, unit: "can", name: "chickpeas", prep_note: "drained"}` at
import time. Everything after that is deterministic.

## Why it's built this way

The three things you actually want from a recipe manager are a query, a join,
and a sum:

| Task | What it really is |
| --- | --- |
| Recipes containing an ingredient | `SELECT` with a join |
| Shopping list for a recipe | Join recipes to ingredients |
| Merge duplicate ingredients across recipes | Unit normalization and `SUM` |

None of those are language tasks, and a model asked to do them from raw recipe
text will be inconsistent — especially the third, where `2 cloves garlic` and
`1 tbsp minced garlic` have to reconcile into a single line. That needs
normalized data and a conversion table.

So the data and logic live locally in SQLite, and the model is called once per
recipe, at import, to do the parsing that regexes lose at.

## Requirements

- Python 3.9+
- `requests`, `beautifulsoup4`, `lxml`
- An Anthropic API key

```bash
pip install requests beautifulsoup4 lxml
export ANTHROPIC_API_KEY="sk-ant-..."
```

Keys come from the Claude Console at platform.claude.com under Settings → API
keys. The key is shown only once at creation.

**API billing is separate from a Claude.ai subscription.** Pro and Max plans do
not include Console credits, and there is no free tier — you buy prepaid credits
in the Console. At one short request per recipe the cost is negligible; the
minimum credit purchase will outlast the project. To trim further, change
`MODEL` in `resolve.py` to `claude-haiku-4-5-20251001`.

## Setup

```bash
sqlite3 recipes.db < schema.sql
sqlite3 recipes.db < substitutions.sql
```

## Data model

**`ingredients`** — one row per real-world thing. `canonical_name` is the name
everything else resolves to. `grams_per_cup` and `grams_per_count` are the
bridges that let volume, weight, and count amounts be summed together; both are
nullable and only need filling in when an ingredient actually appears in mixed
units.

**`ingredient_aliases`** — alternate spellings pointing at a canonical row.
`scallion` → `green onion`. Two names, one thing.

**`recipe_ingredients`** — the parsed lines. `prep_note` holds *minced*,
*divided*, *drained*, so the ingredient name stays clean and garlic is always
garlic. `raw_text` preserves the original line for auditing.

**`substitutions`** — different ingredients that can stand in for each other, at
a conversion ratio, in a direction. Distinct from aliases.

**`units`** — static conversion table. Volume converts to ml, weight to grams,
count stays as-is.

**`v_normalized`** — every amount resolved to a base unit, so aggregation is a
plain `GROUP BY`. This is what makes shopping lists work.

## Workflow

### 1. Import

```bash
python3 ingest.py https://somesite.com/chickpea-stew
python3 ingest.py --file saved_page.html
```

Extracts schema.org `Recipe` JSON-LD from the page and writes
`inbox/<slug>.json`. No model involved and no database write. Handles the shapes
that break naive parsers: recipes nested in `@graph`, `@type` as an array,
`recipeYield` as a list, instructions wrapped in `HowToSection`.

Most recipe sites emit this — AllRecipes, Serious Eats, NYT Cooking, Bon
Appétit, and most WordPress food blogs via WP Recipe Maker. Sites that don't will
exit with a message, and you fall back to manual entry.

### 2. Parse and review

```bash
python3 resolve.py inbox/chickpea-stew.json
```

Sends all ingredient lines to the model in one request, prints the parse, and
writes nothing. Ingredients not yet in your tables are marked `+`.

The parse is cached back into the inbox file as a `parsed` array. Rerunning
reads the cache instead of calling the API again.

| Flag | Effect |
| --- | --- |
| `--commit` | Write the recipe to the database |
| `--reparse` | Ignore the cache and call the model again |

### 3. Commit

```bash
python3 resolve.py inbox/chickpea-stew.json --commit
```

Prints the new recipe id.

**Don't delete `inbox/` afterward.** Those files are your provenance trail:
original page data, model output, and any hand corrections, one file per recipe.

## Fixing a bad parse

Three different failures with three different right answers.

**Naming mismatch** — the model returns `garlic cloves` where your table has
`garlic`, showing as new with a `+`. Most of what you'll see. Don't edit JSON;
add an alias, which fixes this recipe and every future one:

```bash
python3 alias.py "garlic cloves" garlic
```

**Systematic misparse** — the model keeps making the same category of mistake.
Fix the `SYSTEM` prompt in `resolve.py` and add that exact line as an example.
Worth doing after you've seen it twice.

**Genuine one-off** — source typo, or an ambiguous line like
`1 small handful parsley`. Now edit the `parsed` array in the inbox file and
rerun.

### Row-count mismatch

```
Parser returned 15 rows for 14 lines.
```

The model split one line into two — usually a compound line like
`Salt and pepper to taste`. The error prints both columns side by side with `>>`
marking the first row where they diverge; everything above that is fine.

Open the inbox file, merge the split objects or delete the extra so `parsed`
matches `ingredient_lines` in length, and rerun. If it recurs, add to `SYSTEM`:

```
Return exactly one object per input line, even when a line names two
ingredients. "Salt and pepper to taste" is one object, not two.
```

## Commands

### `alias.py` — two names, one ingredient

```bash
python3 alias.py scallion "green onion"     # losing name first
python3 alias.py --list
python3 alias.py --delete scallion
python3 alias.py scallion onion --retarget
```

If the losing name already exists as its own ingredient row, its recipe
references are moved onto the canonical row and the duplicate is deleted.

Retargeting refuses without `--retarget` and reports what the alias currently
points at.

**Caveat:** delete and retarget only affect *future* parses. Rows already merged
stay merged — the alias table governs how names resolve going forward, it isn't
a live link. Undoing a bad merge is two jobs: fix the alias, then repoint the
affected rows yourself using `raw_text` to find them.

```sql
UPDATE recipe_ingredients
SET ingredient_id = (SELECT id FROM ingredients WHERE canonical_name = 'shallot')
WHERE raw_text LIKE '%shallot%'
  AND ingredient_id = (SELECT id FROM ingredients WHERE canonical_name = 'garlic');
```

### `edit.py` — change a committed recipe

```bash
python3 edit.py --list                    # find recipe ids
python3 edit.py 3 show
python3 edit.py 3 set garlic 5 clove
python3 edit.py 3 drop "heavy cream"
python3 edit.py 3 add "smoked paprika" 1 tsp --prep toasted
python3 edit.py 3 note "Cut the cream, lemon at the end."
python3 edit.py 3 fork "Clam Chowder (my version)"
```

The leading number is the recipe id — the primary key SQLite assigns on commit,
which `resolve.py` prints. `--list` shows them all.

`set`/`drop`/`add` mutate in place; use them to correct wrong data. `fork` copies
the recipe and its ingredients into a new row linked back via `variant_of`, so
you can change the copy and keep the original. Adds the `variant_of` column on
first run — no separate migration.

**Caveats:**

- Editing a row clears its `raw_text` and `show` marks it `*edited`. That column
  means "this is what the source said," and after an edit it doesn't.
- `note` overwrites rather than appends.
- `set` refuses unknown ingredients; use `add` to create one.
- Forking on every tweak buries you in near-duplicates that all match the same
  ingredient searches. Edit in place by default; fork only when you'd genuinely
  cook both versions.

### `sub.py` — one ingredient stands in for another

```bash
python3 sub.py "littleneck clam" 10 whole "chopped clams" 4 oz \
    --quality 2 --note "Reserve the juice; cut added salt." --both
python3 sub.py --list
python3 sub.py --delete 3
```

Six positional arguments in fixed order: what the recipe calls for, then what
you'd use instead. Use `none` as a unit for ingredients recorded without one.

| Flag | Effect |
| --- | --- |
| `--both` | Also record the reverse swap |
| `--quality N` | 1 = as good, 2 = fine (default), 3 = in a pinch |
| `--note TEXT` | What to watch out for |
| `--recipe N` | Limit this swap to one recipe instead of everywhere |

Substitutions are stored as a quantity *pair* (10 whole ↔ 4 oz), not a scalar,
because the two sides are often in different dimensions. The `v_substitutions`
view divides through, so scaling a recipe to 25 clams correctly reports 10 oz
canned.

Direction is explicit. Fresh→canned is a compromise; canned→fresh is an upgrade.
One row can't say both, which is what `--both` is for.

**Caveats:**

- Nothing auto-applies. The view is advisory — your shopping list still says what
  the recipe says. A system that quietly rewrites recipes is one where you can't
  tell why the chowder tastes different.
- Both ingredients must already exist. In practice the substitute needs to appear
  in a recipe first, or be inserted by hand.
- **The view matches on exact unit equality.** A rule stored as `10 whole` won't
  fire on a recipe that recorded clams as `2 lb`, even though it's the same
  ingredient and the same valid swap. Two ways out: add a second rule keyed to
  the other unit (cheap, doesn't scale), or rewrite the view's join to compare
  normalized grams the way `v_normalized` does (proper, needs `grams_per_count`
  populated). Do the second once you have enough recipes to actually hit it.
- Global rows (`recipe_id IS NULL`) need a partial unique index to catch
  duplicates, because SQLite treats NULLs as distinct in `UNIQUE` constraints.
  `sub.py` creates it on startup; clear existing duplicates first or it won't
  build.

## Expected friction

Your first few recipes will add most of their ingredients as new rows, and
near-duplicates will creep in — `scallion` one week, `green onion` the next.
That's what aliases are for. After twenty or thirty recipes the ingredient table
stabilizes and new recipes mostly hit existing rows.

Review the parse before committing. Parse errors are silent poison: a wrong unit
doesn't throw, it quietly corrupts every future shopping list touching that
ingredient. Ten seconds per recipe, paid once.

If one canonical ingredient collects six aliases, your parse prompt could
probably name it more consistently in the first place.

## Files

| File | Purpose |
| --- | --- |
| `schema.sql` | Core tables, units, `v_normalized` |
| `substitutions.sql` | Substitutions table and `v_substitutions` |
| `ingest.py` | URL → structured JSON in `inbox/` |
| `resolve.py` | Parse ingredient lines, review, commit |
| `alias.py` | Manage ingredient aliases |
| `edit.py` | Edit and fork committed recipes |
| `sub.py` | Manage substitutions |

## Not yet built

The MCP server that exposes the database to Claude as a chat interface. That's
the query layer — recipes by ingredient, shopping lists, cross-recipe merging —
and it's all reads against the views above.
