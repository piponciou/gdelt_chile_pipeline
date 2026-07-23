"""
Extracción histórica de noticias de Chile desde GDELT GKG (BigQuery) hacia PostgreSQL.

Recorre mes a mes desde febrero de 2015 hasta el mes actual. Es reanudable:
si se interrumpe, la próxima ejecución retoma desde el primer mes no completado,
usando la tabla batch_control como registro de avance.

Requisitos previos:
  - Haber ejecutado sql/schema.sql contra la base de destino.
  - Tener Google Cloud CLI autenticado (gcloud auth application-default login).
  - pip install -r requirements.txt
  - Copiar .env.example a .env y completar tus propios valores.

Ejecutar desde la raíz del proyecto: python scripts/extraccion_historica.py
"""

import time
from datetime import date, timedelta

from pipeline_core import crear_bq_client, crear_pg_conn, procesar_rango


def main():
    bq_client = crear_bq_client()
    pg_conn = crear_pg_conn()
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
            n = procesar_rango(bq_client, cur, current, next_month)
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
