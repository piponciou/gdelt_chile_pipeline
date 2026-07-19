-- =========================================================
-- Transforma los datos crudos de staging_gkg y los carga en articles.
-- Se ejecuta una vez por cada lote (mes) cargado en staging_gkg.
--
-- Requiere que sql/schema.sql ya haya sido ejecutado (usa las
-- funciones safe_int / safe_numeric definidas ahí).
-- =========================================================

INSERT INTO articles (
    gkg_record_id, published_at, media_type, media_outlet, url,
    tone_avg, tone_raw,
    theme_codes, organization_names, person_names,
    location_mentions,
    mentioned_amounts, mentioned_quotes, additional_names,
    loaded_at, extraction_batch_id
)
SELECT
    s.gkgrecordid,
    TO_TIMESTAMP(s.date, 'YYYYMMDDHH24MISS'),
    s.sourcecollectionidentifier,
    s.sourcecommonname,
    s.documentidentifier,
    safe_numeric(split_part(s.v2tone, ',', 1)),
    s.v2tone,

    -- Themes: vocabulario controlado, se deduplica y se quita el offset de posición
    (SELECT ARRAY(
        SELECT DISTINCT regexp_replace(t, ',\d+$', '')
        FROM unnest(string_to_array(s.v2themes, ';')) AS t
        WHERE t <> ''
     )),

    -- Organizations: texto libre, sin resolución de entidades
    (SELECT ARRAY(
        SELECT DISTINCT regexp_replace(o, ',\d+$', '')
        FROM unnest(string_to_array(s.v2organizations, ';')) AS o
        WHERE o <> ''
     )),

    -- Persons: texto libre, sin resolución de entidades
    (SELECT ARRAY(
        SELECT DISTINCT regexp_replace(p, ',\d+$', '')
        FROM unnest(string_to_array(s.v2persons, ';')) AS p
        WHERE p <> ''
     )),

    -- Locations: cada mención se conserva individualmente (con su offset).
    -- Formato esperado: "tipo#nombre#pais#adm1#adm2#lat#long#featureid#offset".
    -- GDELT entrega ocasionalmente bloques con un campo de más (ver docs/ARQUITECTURA.md);
    -- safe_int/safe_numeric evitan que eso tumbe la carga, a costa de perder
    -- solo el dato puntual mal ubicado (normalmente el offset de esa mención).
    (SELECT jsonb_agg(
        jsonb_build_object(
            'location_type', safe_int(split_part(loc, '#', 1)),
            'name', split_part(loc, '#', 2),
            'country_code', split_part(loc, '#', 3),
            'adm1_code', NULLIF(split_part(loc, '#', 4), ''),
            'adm2_code', NULLIF(split_part(loc, '#', 5), ''),
            'lat', safe_numeric(split_part(loc, '#', 6)),
            'long', safe_numeric(split_part(loc, '#', 7)),
            'feature_id', split_part(loc, '#', 8),
            'char_offset', safe_int(split_part(loc, '#', 9))
        )
     )
     FROM unnest(string_to_array(s.v2locations, ';')) AS loc
     WHERE loc <> ''),

    -- Amounts: formato confirmado "cantidad,objeto,offset". Regex en vez de split_part
    -- porque "objeto" puede contener comas internas.
    (SELECT jsonb_agg(
        jsonb_build_object(
            'amount', safe_numeric(m[1]),
            'object', m[2],
            'char_offset', safe_int(m[3])
        )
     )
     FROM unnest(string_to_array(s.amounts, ';')) AS blk
     CROSS JOIN LATERAL regexp_match(blk, '^([\d.]+),(.*),(\d+)$') AS m
     WHERE blk <> ''),

    -- Quotations: NO se parsea (delimitador interno no confirmado con ejemplos reales).
    -- Se guarda crudo para no arriesgar integridad de los datos. Buscable con LIKE.
    to_jsonb(NULLIF(s.quotations, '')),

    -- AllNames: formato confirmado "nombre,offset"
    (SELECT jsonb_agg(
        jsonb_build_object(
            'name', m[1],
            'char_offset', safe_int(m[2])
        )
     )
     FROM unnest(string_to_array(s.allnames, ';')) AS blk
     CROSS JOIN LATERAL regexp_match(blk, '^(.*),(\d+)$') AS m
     WHERE blk <> ''),

    NOW(),
    'historico'
FROM (
    -- GDELT puede entregar más de una copia del mismo GKGRECORDID dentro del
    -- mismo lote, y no todas las copias vienen igual de completas (se observó
    -- en la práctica un caso con V2Themes vacío en una copia y completo en otra
    -- del mismo ID). Esta subconsulta se queda con la copia más completa de
    -- cada GKGRECORDID repetido antes de insertar, en vez de dejar que
    -- ON CONFLICT descarte una copia al azar.
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY s.gkgrecordid
               ORDER BY
                   (CASE WHEN s.v2themes IS NOT NULL AND s.v2themes <> '' THEN 1 ELSE 0 END) DESC,
                   LENGTH(
                       COALESCE(s.v2themes, '') || COALESCE(s.v2organizations, '') ||
                       COALESCE(s.v2persons, '') || COALESCE(s.v2locations, '')
                   ) DESC
           ) AS rn
    FROM staging_gkg s
) s
WHERE s.rn = 1
ON CONFLICT (gkg_record_id) DO NOTHING;
-- ON CONFLICT: red de seguridad adicional para duplicados que persistan
-- entre lotes distintos (ej. el mismo artículo reprocesado en dos meses).
