PRAGMA foreign_keys = ON;

-- Canonical ingredients. One row per real-world thing.
CREATE TABLE ingredients (
    id              INTEGER PRIMARY KEY,
    canonical_name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aisle           TEXT,           -- for grouping the shopping list
    grams_per_cup   REAL,           -- lets volume and weight amounts be summed
    grams_per_count REAL            -- weight of 1 clove / 1 whole / 1 bunch
);

-- "scallion" -> "green onion", "garbanzo" -> "chickpea"
CREATE TABLE ingredient_aliases (
    alias           TEXT PRIMARY KEY COLLATE NOCASE,
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE
);

-- Static unit table. dimension is 'volume', 'weight', or 'count'.
-- to_base converts to ml (volume) or g (weight).
CREATE TABLE units (
    name        TEXT PRIMARY KEY COLLATE NOCASE,
    dimension   TEXT NOT NULL CHECK (dimension IN ('volume','weight','count')),
    to_base     REAL NOT NULL
);

CREATE TABLE recipes (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    source_url      TEXT,
    servings        REAL,
    instructions    TEXT,
    notes           TEXT,
    added_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE recipe_ingredients (
    id              INTEGER PRIMARY KEY,
    recipe_id       INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    quantity        REAL,           -- NULL = "to taste"
    unit            TEXT REFERENCES units(name),
    prep_note       TEXT,           -- minced, chopped, divided, room temperature
    optional        INTEGER NOT NULL DEFAULT 0,
    raw_text        TEXT            -- the original line, kept for auditing the parse
);

CREATE INDEX idx_ri_recipe     ON recipe_ingredients(recipe_id);
CREATE INDEX idx_ri_ingredient ON recipe_ingredients(ingredient_id);

-- Every amount resolved to a base unit, so aggregation is a plain SUM.
CREATE VIEW v_normalized AS
SELECT
    ri.recipe_id,
    ri.ingredient_id,
    i.canonical_name,
    i.aisle,
    ri.quantity,
    ri.unit,
    u.dimension,
    CASE
        WHEN u.dimension = 'weight' THEN ri.quantity * u.to_base
        WHEN u.dimension = 'volume' AND i.grams_per_cup IS NOT NULL
             THEN ri.quantity * u.to_base / 236.588 * i.grams_per_cup
        WHEN u.dimension = 'count' AND i.grams_per_count IS NOT NULL
             THEN ri.quantity * i.grams_per_count
        ELSE NULL
    END AS grams,
    CASE WHEN u.dimension = 'volume' THEN ri.quantity * u.to_base END AS ml,
    CASE WHEN u.dimension = 'count'  THEN ri.quantity END AS count,
    ri.prep_note,
    ri.optional
FROM recipe_ingredients ri
JOIN ingredients i ON i.id = ri.ingredient_id
LEFT JOIN units  u ON u.name = ri.unit;

INSERT INTO units (name, dimension, to_base) VALUES
    ('tsp',    'volume', 4.92892),
    ('tbsp',   'volume', 14.7868),
    ('cup',    'volume', 236.588),
    ('fl oz',  'volume', 29.5735),
    ('ml',     'volume', 1.0),
    ('l',      'volume', 1000.0),
    ('pint',   'volume', 473.176),
    ('quart',  'volume', 946.353),
    ('g',      'weight', 1.0),
    ('kg',     'weight', 1000.0),
    ('oz',     'weight', 28.3495),
    ('lb',     'weight', 453.592),
    ('whole',  'count',  1.0),
    ('clove',  'count',  1.0),
    ('bunch',  'count',  1.0),
    ('can',    'count',  1.0),
    ('pinch',  'count',  1.0);
