"""
Módulo compartido del pipeline GDELT: configuración de conexión y la función
de extracción/transformación reutilizada tanto por la carga histórica
(extraccion_historica.py) como por la actualización incremental diaria
(actualizacion_diaria.py).
"""

import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from google.cloud import bigquery
from psycopg2.extras import execute_values

load_dotenv()

PROYECTO_GCP = os.environ["GCP_PROJECT_ID"]
PG_CONFIG = dict(
    host=os.environ.get("PG_HOST", "localhost"),
    dbname=os.environ["PG_DATABASE"],
    user=os.environ.get("PG_USER", "postgres"),
    password=os.environ["PG_PASSWORD"],
)


def crear_bq_client():
    return bigquery.Client(project=PROYECTO_GCP)


def crear_pg_conn():
    conn = psycopg2.connect(**PG_CONFIG)
    conn.autocommit = True
    return conn


def procesar_rango(bq_client, cur, fecha_inicio, fecha_fin):
    """
    Extrae de BigQuery las noticias de Chile publicadas entre fecha_inicio (incluida)
    y fecha_fin (excluida), las deja en staging_gkg, y las transforma hacia articles.

    El tamaño del rango es indiferente para esta función: sirve igual para un mes
    completo (carga histórica) que para un solo día (actualización incremental).
    """
    query = f"""
        SELECT GKGRECORDID, DATE, SourceCollectionIdentifier, SourceCommonName,
               DocumentIdentifier, V2Tone, V2Themes, V2Organizations, V2Persons,
               V2Locations, Amounts, Quotations, AllNames
        FROM `gdelt-bq.gdeltv2.gkg_partitioned`
        WHERE _PARTITIONTIME BETWEEN TIMESTAMP('{fecha_inicio}') AND TIMESTAMP('{fecha_fin}')
          AND (V2Locations LIKE '%#CI#%' OR SourceCommonName LIKE '%.cl%')
    """
    df = bq_client.query(query).to_dataframe(create_bqstorage_client=True)
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)
    rows = list(df.itertuples(index=False, name=None))

    cur.execute("TRUNCATE staging_gkg")
    if rows:
        execute_values(cur, "INSERT INTO staging_gkg VALUES %s", rows)
        cur.execute(open("sql/transformar.sql", encoding="utf-8").read())

    return len(rows)
