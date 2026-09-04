-- Substitutions: different ingredients that can stand in for one another.
-- Distinct from ingredient_aliases, which is for two names of the same thing.
--
-- Directional by design. Canned clams substitute for fresh in a chowder;
-- the reverse is true too, but plenty of pairs only work one way (buttermilk
-- from milk + vinegar is fine; you can't go backward).

CREATE TABLE substitutions (
    id              INTEGER PRIMARY KEY,

    from_ingredient INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    from_quantity   REAL    NOT NULL,
    from_unit       TEXT    REFERENCES units(name),

    to_ingredient   INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    to_quantity     REAL    NOT NULL,
    to_unit         TEXT    REFERENCES units(name),

    -- NULL = applies anywhere. Set it to pin a swap to a single recipe you've
    -- already tested, without asserting it works in general.
    recipe_id       INTEGER REFERENCES recipes(id) ON DELETE CASCADE,

    -- 1 = as good as the original, 2 = fine, 3 = works in a pinch
    quality         INTEGER NOT NULL DEFAULT 2 CHECK (quality BETWEEN 1 AND 3),

    notes           TEXT,

    UNIQUE (from_ingredient, to_ingredient, recipe_id)
);

CREATE INDEX idx_sub_from ON substitutions(from_ingredient);
CREATE INDEX idx_sub_to   ON substitutions(to_ingredient);

-- What could I swap in, for every ingredient in every recipe?
-- Resolves the ratio against the amount the recipe actually calls for.
CREATE VIEW v_substitutions AS
SELECT
    ri.recipe_id,
    r.title,
    src.canonical_name       AS calls_for,
    ri.quantity              AS calls_for_qty,
    ri.unit                  AS calls_for_unit,
    dst.canonical_name       AS substitute,
    ROUND(ri.quantity / s.from_quantity * s.to_quantity, 2) AS substitute_qty,
    s.to_unit                AS substitute_unit,
    s.quality,
    s.notes,
    s.recipe_id IS NOT NULL  AS recipe_specific
FROM recipe_ingredients ri
JOIN recipes     r   ON r.id  = ri.recipe_id
JOIN ingredients src ON src.id = ri.ingredient_id
JOIN substitutions s ON s.from_ingredient = ri.ingredient_id
                    AND (s.recipe_id IS NULL OR s.recipe_id = ri.recipe_id)
JOIN ingredients dst ON dst.id = s.to_ingredient
WHERE ri.unit IS NOT DISTINCT FROM s.from_unit;
