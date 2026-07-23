-- =========================================================
-- Esquema de la base de datos de noticias de Chile (GDELT GKG)
-- Ejecutar contra una base PostgreSQL vacía recién creada.
-- =========================================================

-- Tabla principal: una fila por noticia, ya transformada y lista para consultar
CREATE TABLE articles (
    gkg_record_id         VARCHAR PRIMARY KEY,
    published_at          TIMESTAMP,
    media_type            VARCHAR,
    media_outlet          VARCHAR,
    url                   TEXT,
    title                 TEXT,

    tone_avg              NUMERIC,
    tone_raw              TEXT,

    theme_codes           TEXT[],
    organization_names    TEXT[],
    person_names          TEXT[],
    location_mentions     JSONB,

    mentioned_amounts     JSONB,
    mentioned_quotes      JSONB,
    additional_names      JSONB,

    loaded_at             TIMESTAMP DEFAULT NOW(),
    extraction_batch_id   VARCHAR
);

CREATE INDEX idx_articles_published_at ON articles (published_at);
CREATE INDEX idx_articles_media_outlet ON articles (media_outlet);
CREATE INDEX idx_articles_theme_codes ON articles USING GIN (theme_codes);
CREATE INDEX idx_articles_organization_names ON articles USING GIN (organization_names);
CREATE INDEX idx_articles_person_names ON articles USING GIN (person_names);
CREATE INDEX idx_articles_location_mentions ON articles USING GIN (location_mentions);

COMMENT ON TABLE articles IS 'Noticias de Chile extraídas desde GDELT GKG 2.0. Fuente: gdelt-bq.gdeltv2.gkg_partitioned';
COMMENT ON COLUMN articles.gkg_record_id IS 'GKGRECORDID original de GDELT';
COMMENT ON COLUMN articles.published_at IS 'Campo DATE de GKG, convertido a timestamp';
COMMENT ON COLUMN articles.media_type IS 'SourceCollectionIdentifier de GDELT (tipo de fuente)';
COMMENT ON COLUMN articles.media_outlet IS 'SourceCommonName de GDELT (medio/dominio)';
COMMENT ON COLUMN articles.url IS 'DocumentIdentifier de GDELT';
COMMENT ON COLUMN articles.title IS 'No siempre disponible en GKG';
COMMENT ON COLUMN articles.tone_avg IS 'Posición 0 de V2Tone: tono promedio del artículo (-100 a +100)';
COMMENT ON COLUMN articles.tone_raw IS 'String completo de V2Tone sin procesar';
COMMENT ON COLUMN articles.theme_codes IS 'V2Themes parseado como array de códigos únicos';
COMMENT ON COLUMN articles.organization_names IS 'V2Organizations parseado como array (texto libre, sin resolución de entidades)';
COMMENT ON COLUMN articles.person_names IS 'V2Persons parseado como array (texto libre, sin resolución de entidades)';
COMMENT ON COLUMN articles.location_mentions IS 'V2Locations parseado como array JSONB de objetos con país/región/coordenadas';
COMMENT ON COLUMN articles.mentioned_amounts IS 'Campo Amounts de GKG, parseado como JSONB';
COMMENT ON COLUMN articles.mentioned_quotes IS 'Campo Quotations de GKG, guardado CRUDO (sin parsear) por falta de confirmación del delimitador interno';
COMMENT ON COLUMN articles.additional_names IS 'Campo AllNames de GKG, parseado como JSONB';
COMMENT ON COLUMN articles.loaded_at IS 'Metadato propio del pipeline: cuándo se insertó esta fila';
COMMENT ON COLUMN articles.extraction_batch_id IS 'Metadato propio del pipeline: identificador de la ejecución que trajo esta fila';


-- Tabla de staging (aterrizaje): recibe los datos crudos de BigQuery, tal cual, antes de transformar
CREATE TABLE staging_gkg (
    gkgrecordid TEXT,
    date TEXT,
    sourcecollectionidentifier TEXT,
    sourcecommonname TEXT,
    documentidentifier TEXT,
    v2tone TEXT,
    v2themes TEXT,
    v2organizations TEXT,
    v2persons TEXT,
    v2locations TEXT,
    amounts TEXT,
    quotations TEXT,
    allnames TEXT
);


-- Tabla de control: registra qué meses ya fueron procesados, para poder pausar/reanudar el pipeline histórico
CREATE TABLE batch_control (
    month_start DATE PRIMARY KEY,
    status VARCHAR,
    rows_loaded INT,
    processed_at TIMESTAMP DEFAULT NOW()
);


-- Tabla de control diario: registra qué días fueron procesados por la
-- actualización incremental (scripts/actualizacion_diaria.py). Separada de
-- batch_control para no mezclar la granularidad de meses históricos con
-- días incrementales.
CREATE TABLE daily_control (
    day_start DATE PRIMARY KEY,
    status VARCHAR,
    rows_loaded INT,
    processed_at TIMESTAMP DEFAULT NOW()
);


-- =========================================================
-- Funciones de conversión segura.
-- GDELT ocasionalmente entrega bloques de datos malformados
-- (ver docs/ARQUITECTURA.md, sección "Manejo de datos malformados").
-- Estas funciones evitan que un solo valor corrupto tumbe la carga
-- completa de un lote: si la conversión falla, devuelven NULL en
-- vez de lanzar un error.
-- =========================================================

CREATE OR REPLACE FUNCTION safe_int(text) RETURNS int AS $$
BEGIN
    RETURN $1::int;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION safe_numeric(text) RETURNS numeric AS $$
BEGIN
    RETURN $1::numeric;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
