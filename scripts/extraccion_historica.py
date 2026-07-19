"""
Extracción histórica de noticias de Chile desde GDELT GKG (BigQuery) hacia PostgreSQL.

Recorre mes a mes desde febrero de 2015 hasta el mes actual. Es reanudable:
si se interrumpe, la próxima ejecución retoma desde el primer mes no completado,
usando la tabla batch_control como registro de avance.

Requisitos previos:
  - Haber ejecutado sql/schema.sql contra la base de destino.
  - Tener Google Cloud CLI autenticado (gcloud auth application-default login).
  - pip install -r requirements.txt
  - Copiar .env.example a .env y completar tus propios valores (nunca subir .env a Git).
"""

import os
import time
from datetime import date, timedelta

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


def procesar_mes(bq_client, cur, fecha_inicio, fecha_fin):
    """Extrae un mes desde BigQuery, lo deja en staging_gkg, y lo transforma hacia articles."""
    query = f"""
        SELECT GKGRECORDID, DATE, SourceCollectionIdentifier, SourceCommonName,
               DocumentIdentifier, V2Tone, V2Themes, V2Organizations, V2Persons,
               V2Locations, Amounts, Quotations, AllNames
        FROM `gdelt-bq.gdeltv2.gkg_partitioned`
        WHERE _PARTITIONTIME BETWEEN TIMESTAMP('{fecha_inicio}') AND TIMESTAMP('{fecha_fin}')
          AND (V2Locations LIKE '%#CI#%' OR SourceCommonName LIKE '%.cl%')
    """
    # Se usa to_dataframe(create_bqstorage_client=True) en vez de iterar result() fila a fila:
    # la BigQuery Storage API baja los datos en bloque y es varias veces más rápida
    # para volúmenes de decenas de miles de filas.
    df = bq_client.query(query).to_dataframe(create_bqstorage_client=True)
    df = df.astype(object)  # evita tipos numpy (ej. numpy.int64) que psycopg2 no adapta
    df = df.where(pd.notnull(df), None)  # NaN -> None, para que Postgres reciba NULL
    rows = list(df.itertuples(index=False, name=None))

    cur.execute("TRUNCATE staging_gkg")
    if rows:
        execute_values(cur, "INSERT INTO staging_gkg VALUES %s", rows)
        cur.execute(open("sql/transformar.sql", encoding="utf-8").read())

    return len(rows)


def main():
    bq_client = bigquery.Client(project=PROYECTO_GCP)
    pg_conn = psycopg2.connect(**PG_CONFIG)
    pg_conn.autocommit = True
    cur = pg_conn.cursor()

    current = date(2015, 2, 1)
    end = date.today().replace(day=1)

    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

        cur.execute(
            "SELECT 1 FROM batch_control WHERE month_start = %s AND status = 'done'",
            (current,),
        )
        if cur.fetchone():
            print(f"{current} ya procesado, saltando")
            current = next_month
            continue

        print(f"Procesando {current}...")
        try:
            t0 = time.time()
            n = procesar_mes(bq_client, cur, current, next_month)
            duracion = time.time() - t0
            cur.execute(
                """
                INSERT INTO batch_control (month_start, status, rows_loaded)
                VALUES (%s, 'done', %s)
                ON CONFLICT (month_start) DO UPDATE SET status='done', rows_loaded=%s
                """,
                (current, n, n),
            )
            print(f"{current}: {n} filas cargadas en {duracion:.1f} segundos")
        except Exception as e:
            print(f"ERROR en {current}: {e}")
            cur.execute(
                """
                INSERT INTO batch_control (month_start, status, rows_loaded)
                VALUES (%s, 'error', 0)
                ON CONFLICT (month_start) DO UPDATE SET status='error'
                """,
                (current,),
            )

        current = next_month

    cur.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
