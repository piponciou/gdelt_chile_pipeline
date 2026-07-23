"""
Actualización incremental diaria de noticias de Chile desde GDELT GKG.

A diferencia del script histórico (que recorre un rango fijo de meses),
este script determina automáticamente desde dónde continuar: consulta la
fecha más reciente ya cargada en articles, y procesa día por día desde el
siguiente hasta ayer. Esto lo hace robusto frente a interrupciones (ej. el
computador estuvo apagado varios días): la próxima ejecución simplemente
procesa todos los días pendientes de una vez, sin intervención manual.

Pensado para ser disparado automáticamente por un programador de tareas del
sistema operativo (ej. Task Scheduler en Windows), una vez al día.

Requisitos previos: los mismos que extraccion_historica.py, más haber
ejecutado la creación de la tabla daily_control (ver sql/schema.sql).

Ejecutar desde la raíz del proyecto: python scripts/actualizacion_diaria.py
"""

import time
from datetime import date, timedelta

from pipeline_core import crear_bq_client, crear_pg_conn, procesar_rango


def main():
    bq_client = crear_bq_client()
    pg_conn = crear_pg_conn()
    cur = pg_conn.cursor()

    cur.execute("SELECT MAX(published_at)::date FROM articles")
    ultimo_cargado = cur.fetchone()[0]

    if ultimo_cargado is None:
        print("No hay datos previos en 'articles'. Corre primero extraccion_historica.py.")
        cur.close()
        pg_conn.close()
        return

    dia = ultimo_cargado + timedelta(days=1)
    ayer = date.today() - timedelta(days=1)

    if dia > ayer:
        print(f"La base ya está al día (último día cargado: {ultimo_cargado}). Nada que hacer.")
        cur.close()
        pg_conn.close()
        return

    print(f"Último día cargado: {ultimo_cargado}. Procesando desde {dia} hasta {ayer}.")

    while dia <= ayer:
        siguiente = dia + timedelta(days=1)

        cur.execute(
            "SELECT 1 FROM daily_control WHERE day_start = %s AND status = 'done'",
            (dia,),
        )
        if cur.fetchone():
            print(f"{dia} ya procesado, saltando")
            dia = siguiente
            continue

        print(f"Procesando {dia}...")
        try:
            t0 = time.time()
            n = procesar_rango(bq_client, cur, dia, siguiente)
            duracion = time.time() - t0
            cur.execute(
                """
                INSERT INTO daily_control (day_start, status, rows_loaded)
                VALUES (%s, 'done', %s)
                ON CONFLICT (day_start) DO UPDATE SET status='done', rows_loaded=%s
                """,
                (dia, n, n),
            )
            print(f"{dia}: {n} filas cargadas en {duracion:.1f} segundos")
        except Exception as e:
            print(f"ERROR en {dia}: {e}")
            cur.execute(
                """
                INSERT INTO daily_control (day_start, status, rows_loaded)
                VALUES (%s, 'error', 0)
                ON CONFLICT (day_start) DO UPDATE SET status='error'
                """,
                (dia,),
            )

        dia = siguiente

    cur.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
