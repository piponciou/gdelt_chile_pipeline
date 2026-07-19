<h1 align="center">🌎 Base de datos de noticias de Chile desde GDELT</h1>
<p align="center"><i>Infraestructura de datos para investigación en Finanzas, ESG y áreas afines</i></p>

<hr>

## Tabla de contenidos

1. [Contexto del proyecto](#1-contexto-del-proyecto)
2. [¿Qué es GDELT y por qué se usó específicamente la tabla GKG?](#2-qué-es-gdelt-y-por-qué-se-usó-específicamente-la-tabla-gkg)
3. [Criterio de "noticia relacionada con Chile"](#3-criterio-de-noticia-relacionada-con-chile)
4. [Diseño de la base de datos](#4-diseño-de-la-base-de-datos)
5. [Cómo se obtienen los datos: Google BigQuery](#5-cómo-se-obtienen-los-datos-google-bigquery)
6. [El pipeline de extracción histórica](#6-el-pipeline-de-extracción-histórica)
7. [Limitaciones conocidas](#7-limitaciones-conocidas-documentadas-de-forma-intencional)
8. [Cómo se ejecuta este proyecto de principio a fin](#8-cómo-se-ejecuta-este-proyecto-de-principio-a-fin)
9. [Próximos pasos del proyecto](#9-próximos-pasos-del-proyecto)
10. [Preguntas abiertas](#10-preguntas-abiertas)

> 📊 Las estadísticas descriptivas de la base ya cargada (número de noticias, cobertura temporal, medios, etc.) se presentan en un informe separado, no en este documento.

<hr>

## 1. Contexto del proyecto

Este proyecto responde al primer encargo de práctica: construir una infraestructura de datos reproducible, basada en GDELT, que sirva como fundación para múltiples proyectos de investigación de la Escuela (Finanzas, Contabilidad, ESG, Inteligencia Artificial).

El encargo original comprende tres tareas:

1. **Construir una base histórica** de noticias relacionadas con Chile, desde 2015 hasta hoy, almacenada en PostgreSQL. **Completada** — este documento describe cómo.
2. **Automatizar la actualización diaria** de esa base (pipeline incremental, sin intervención manual). Pendiente.
3. **Generar vistas temáticas** para investigación ESG/Finanzas. En desarrollo.

---

## 2. ¿Qué es GDELT y por qué se usó específicamente la tabla GKG?

GDELT monitorea noticias de todo el mundo y las traduce a datos estructurados. Genera **tres tablas distintas** cada 15 minutos:

| Tabla | Qué modela | ¿Se usa en este proyecto? |
|---|---|---|
| **Events** | Eventos codificados tipo "actor1 hace acción a actor2" (taxonomía CAMEO) | No |
| **Mentions** | Cada vez que un evento de Events es mencionado en un artículo | No |
| **GKG** (Global Knowledge Graph) | Metadatos de **cada artículo de noticias**: tema, organizaciones, personas, ubicaciones, tono | **Sí — es la única que se usa** |

**Por qué GKG y no las otras dos**: la unidad de análisis de GKG es el artículo completo, no un evento estructurado. Cualquier noticia genera un registro en GKG, tenga o no estructura de "evento". Mucho contenido financiero/corporativo (ej. "empresa reporta utilidades") no genera ningún evento CAMEO, así que usar Events habría dejado huecos importantes justo en el tipo de noticias relevantes para Finanzas/ESG. Además, GKG es la única de las tres que trae los campos requeridos por el encargo original: Themes, Organizations, Persons, Locations y Tone.

**Cobertura temporal**: GKG en su formato rico (V2) existe desde el **19 de febrero de 2015**, lo que coincide casi exactamente con el "desde 2015" solicitado.

---

## 3. Criterio de "noticia relacionada con Chile"

Se considera que una fila de GKG es relevante si cumple **al menos una** de estas dos condiciones (unión, no intersección):

- **Criterio A — menciona Chile**: el campo `V2Locations` contiene el código de país `CI`, sin importar qué medio publicó la noticia (ej. Reuters cubriendo la minería chilena).
- **Criterio B — es de un medio chileno**: el campo `SourceCommonName` contiene un dominio `.cl` (ej. emol.com, t13.cl), sin importar de qué país trate la noticia.

**Consecuencia de esta decisión**: como el criterio es "A o B", la base incluye noticias de medios chilenos sin ninguna relación temática con Chile. Un ejemplo real encontrado durante las pruebas: una noticia de t13.cl sobre un ataque a un buque en el Mar Rojo, sin ninguna mención de Chile en el texto — quedó incluida únicamente por venir de un medio chileno. Esta es una decisión metodológica consciente, no un error, pero cualquier análisis posterior debería tenerla presente.

---

## 4. Diseño de la base de datos

### 4.1 Evolución del diseño

El diseño pasó por dos etapas:

1. **Primera versión (normalizada, 9 tablas)**: tabla central `articles` + 4 catálogos (`themes`, `organizations`, `persons`, `locations`) + 4 tablas de unión muchos-a-muchos — el enfoque relacional clásico para atributos multivaluados.
2. **Versión final (simplificada, 1 tabla)**: los catálogos y tablas de unión se reemplazaron por columnas `TEXT[]` (arrays) y `JSONB` directamente en `articles`, indexadas con índices **GIN**.

**Por qué se simplificó**: el patrón de consulta dominante para este proyecto es "¿esta noticia contiene tal theme/organización/ubicación?" — una pregunta de *contención*, no de agregación relacional compleja. PostgreSQL resuelve esto igual de bien (o mejor) con arrays/JSONB + GIN que con joins, evitando además mantener catálogos para campos como Organizations/Persons que son texto libre sin resolución real de entidades (por ejemplo, "Codelco" y "CODELCO" habrían quedado como registros de catálogo distintos de todas formas).

### 4.2 Tabla `articles`

| Columna | Tipo | Origen en GDELT | Notas |
|---|---|---|---|
| `gkg_record_id` | VARCHAR (PK) | `GKGRECORDID` | Identificador único |
| `published_at` | TIMESTAMP | `DATE` | Fecha/hora de publicación |
| `media_type` | VARCHAR | `SourceCollectionIdentifier` | Tipo de fuente |
| `media_outlet` | VARCHAR | `SourceCommonName` | Medio/dominio |
| `url` | TEXT | `DocumentIdentifier` | — |
| `title` | TEXT | *no siempre disponible* | GKG no garantiza este campo |
| `tone_avg` | NUMERIC | Posición 0 de `V2Tone` | Tono promedio (-100 a +100) |
| `tone_raw` | TEXT | `V2Tone` completo | String crudo preservado como fuente de verdad |
| `theme_codes` | TEXT[] | `V2Themes` | Array de códigos de tema, deduplicados |
| `organization_names` | TEXT[] | `V2Organizations` | Array de nombres (texto libre) |
| `person_names` | TEXT[] | `V2Persons` | Array de nombres (texto libre) |
| `location_mentions` | JSONB | `V2Locations` | Array de objetos: tipo, nombre, país, ADM1/2, lat/long, offset |
| `mentioned_amounts` | JSONB | `Amounts` | Array de objetos: monto, objeto, offset |
| `mentioned_quotes` | JSONB | `Quotations` | Guardado crudo, sin parsear (ver sección 7) |
| `additional_names` | JSONB | `AllNames` | Array de objetos: nombre, offset |
| `loaded_at` | TIMESTAMP | — (metadato propio) | Cuándo se insertó la fila |
| `extraction_batch_id` | VARCHAR | — (metadato propio) | Qué ejecución trajo la fila |

**Índices**: B-tree en `published_at` y `media_outlet`; GIN en `theme_codes`, `organization_names`, `person_names`, `location_mentions`.

### 4.3 Tablas auxiliares del pipeline

- **`staging_gkg`**: tabla de aterrizaje. Recibe los datos crudos de GDELT tal cual (todo tipo TEXT, sin transformar), y se vacía (`TRUNCATE`) antes de cada lote nuevo.
- **`batch_control`**: registro de qué meses ya fueron procesados exitosamente (`month_start`, `status`, `rows_loaded`, `processed_at`). Permite pausar y reanudar la extracción histórica sin perder ni duplicar trabajo.

---

## 5. Cómo se obtienen los datos: Google BigQuery

GDELT publica GKG como tabla pública en BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`), accesible gratuitamente vía **BigQuery Sandbox** (1 TB de procesamiento mensual, sin necesidad de tarjeta de crédito).

**Partition pruning**: la tabla está particionada por fecha mediante la columna `_PARTITIONTIME`. Filtrar por esa columna directamente (sin envolverla en funciones como `CAST`) permite que BigQuery se salte por completo las particiones fuera de rango antes de leer cualquier dato. En una prueba real durante el desarrollo, esta optimización redujo el costo de una consulta de **3.48 TB a unos pocos GB** — filtrar la fecha de cualquier otra forma anula esta ventaja y puede agotar la cuota gratuita mensual en una sola consulta.

---

## 6. El pipeline de extracción histórica

### 6.1 Lógica general

El siguiente proceso se repite una vez por cada mes, desde febrero de 2015 hasta el mes actual:

```
Extraer de BigQuery (mes X, filtrado por Chile)
   → Insertar crudo en staging_gkg
   → Transformar y cargar en articles (sql/transformar.sql)
   → Vaciar staging_gkg
   → Registrar el mes como 'done' en batch_control
   → Siguiente mes
```

### 6.2 Por qué por lotes mensuales y no todo de una vez

- Permite pausar y reanudar sin perder trabajo si algo falla a mitad de camino.
- Evita agotar la cuota gratuita mensual de BigQuery en una sola consulta gigante.
- Un error en un mes específico no obliga a reprocesar los demás.

### 6.3 Optimización de velocidad

La primera versión del script descargaba los resultados de BigQuery fila por fila (vía API REST estándar), lo que tomaba en torno a 10 minutos por mes — a razón de 137 meses de histórico, esto habría tomado más de 20 horas en total. Se optimizó usando la **BigQuery Storage API** (`to_dataframe(create_bqstorage_client=True)`), que transfiere los resultados en bloque en vez de fila por fila, reduciendo el tiempo de forma considerable.

### 6.4 Manejo de datos malformados

Durante el desarrollo se identificaron dos problemas reales de calidad de datos, ambos originados en la fuente (GDELT), no en el pipeline:

**a) Bloques de `V2Locations` con un campo de más.** Se encontró, por ejemplo, este bloque real:
```
0#Georgia, , Georgia#GG#GG##42#.5#1#GG#417
```
Este bloque contiene 9 símbolos `#` en vez de los 8 esperados por el formato estándar, lo que desplaza en una posición todos los campos posteriores. En este caso, el offset numérico final terminaba recibiendo el texto `GG` en vez de un número, lo que producía un error de conversión de tipo y detenía la carga completa del mes.

*Solución*: se crearon las funciones `safe_int()` y `safe_numeric()` (definidas en `sql/schema.sql`), que intentan convertir un valor a número y devuelven `NULL` si la conversión falla, en vez de interrumpir todo el proceso. Se aplican a todas las posiciones numéricas de `V2Locations`, `Amounts`, `AllNames` y `V2Tone`. El costo de esta solución es acotado: se pierde únicamente el dato puntual mal ubicado (típicamente el offset de posición de esa mención específica), no la fila completa ni el resto de sus campos válidos.

**b) Duplicados del mismo `GKGRECORDID` con distinto nivel de completitud.** Consultando directamente a BigQuery por un `GKGRECORDID` específico, se confirmó que GDELT puede entregar más de una copia del mismo registro dentro de un mismo lote, y que esas copias no siempre son idénticas entre sí (se encontró un caso concreto con `V2Themes` vacío en una copia y completo en la otra, para el mismo identificador).

*Solución*: en `sql/transformar.sql`, antes de insertar, se numeran las copias de cada `GKGRECORDID` repetido usando `ROW_NUMBER() OVER (PARTITION BY gkgrecordid ORDER BY ...)`, priorizando la copia que tenga `V2Themes` no vacío y, como criterio de desempate, la que tenga mayor contenido combinado entre themes, organizations, persons y locations. Solo se inserta la copia mejor rankeada. La cláusula `ON CONFLICT (gkg_record_id) DO NOTHING`, presente desde una versión anterior del pipeline, se mantiene como red de seguridad adicional para duplicados que puedan aparecer entre lotes distintos.

---

## 7. Limitaciones conocidas (documentadas de forma intencional)

- **`title` puede venir NULL**: GKG no garantiza este campo de forma consistente.
- **`organization_names`/`person_names` no tienen resolución de entidades**: la misma organización puede aparecer escrita de formas distintas en filas distintas, ya que la extracción de GDELT no valida contra un catálogo controlado.
- **`mentioned_quotes` se guarda como texto crudo, no estructurado**: no se logró confirmar con certeza suficiente el delimitador interno del campo `Quotations` (offset, largo, verbo, cita) por falta de ejemplos reales con contenido durante el desarrollo. Dado que un error de parseo podría distorsionar análisis posteriores, se priorizó integridad sobre completitud estructural: el dato completo queda preservado y es buscable con `LIKE`, pero no descompuesto en sub-campos.
- **Bloques de `V2Locations` ocasionalmente malformados** y **duplicados de `GKGRECORDID` con distinta completitud**: ver sección 6.4 — ambos se manejan de forma segura, con pérdida de información acotada y documentada.
- **URLs muertas**: una proporción de las URLs guardadas ya no cargan (páginas dadas de baja, medios reestructurados con el tiempo). Se decidió deliberadamente no filtrar por esto durante la carga: verificar el estado de cada URL a esta escala es costoso, el estado de una URL no es permanente (puede recuperarse o perderse en cualquier momento), y filtrar introduciría un sesgo hacia noticias recientes, ya que las más antiguas son las que con mayor probabilidad ya no existen en línea — distorsionando la cobertura histórica real de la base. El resto de los campos (tono, themes, organizaciones, etc.) permanecen válidos independientemente de si la URL sigue activa.
- **Cobertura desigual entre años**: se observó una correlación entre el número de noticias cargadas por año y el número de medios distintos presentes ese año, lo que sugiere que parte de la variación en volumen a lo largo del tiempo responde a cambios en qué fuentes monitorea GDELT en cada período, y no únicamente a variaciones reales en la cobertura noticiosa de Chile. El detalle numérico de esta observación se presenta en el informe de estadísticas descriptivas.

---

## 8. Cómo se ejecuta este proyecto de principio a fin

1. Instalar PostgreSQL y crear una base de datos vacía (ej. `gdelt_chile_2015`).
2. Ejecutar `sql/schema.sql` contra esa base — crea las tablas, los índices, y las funciones `safe_int`/`safe_numeric`.
3. Crear un proyecto en Google Cloud (gratuito, modo Sandbox, sin tarjeta de crédito) y anotar su ID.
4. Instalar Google Cloud CLI y autenticarse con `gcloud auth application-default login`.
5. Instalar las dependencias de Python listadas en `requirements.txt`.
6. Definir las credenciales del proyecto (ID de Google Cloud, datos de conexión a PostgreSQL) como variables de entorno, siguiendo la plantilla incluida en `.env.example` — este archivo no contiene credenciales reales, solo la estructura esperada.
7. Ejecutar `scripts/extraccion_historica.py` desde la raíz del proyecto.
8. Verificar el resultado revisando `batch_control` (para confirmar que ningún mes quedó en estado de error) y el conteo total de `articles`.

---

## 9. Próximos pasos del proyecto

- **Pipeline incremental diario** (segunda tarea del encargo): reutilizar la misma lógica de extracción del histórico, invocada una sola vez al día con la fecha del día anterior, y disparada automáticamente mediante un programador de tareas del sistema operativo, en lugar de ejecutarse manualmente.
- **Vistas temáticas para investigación ESG/Finanzas** (tercera tarea del encargo): construir vistas SQL sobre `articles`, filtrando por combinaciones curadas de `theme_codes` y `organization_names` relevantes a cada categoría de interés (minería, medio ambiente, sostenibilidad, sistema financiero, gobierno corporativo, fraude y corrupción, entre otras). Este proceso de curación —distinguir themes genuinamente relevantes de themes genéricos que aparecen en cualquier tipo de noticia— requiere revisión caso a caso apoyada en los datos ya cargados.
- **Migración de infraestructura**: la base opera actualmente en una instancia local de PostgreSQL, adecuada para el desarrollo, pero pendiente de migrar a un entorno accesible para el resto de la Escuela.
- **Confirmación del formato de `Quotations`**: pendiente hasta encontrar un registro real con contenido no vacío en ese campo.

---

## 10. Preguntas abiertas

- ¿Dónde debería alojarse la versión operativa final de la base, de forma que sea accesible para otros proyectos de la Escuela?
- ¿Es necesario, a futuro, resolver variantes de nombres en `organization_names`/`person_names` (por ejemplo, unificar "Codelco" y "CODELCO" como la misma entidad)?
- ¿Qué combinación exacta de themes y organizaciones debería definir cada categoría temática de la tercera tarea?

Varias decisiones documentadas en este informe (como el criterio de "noticia relacionada con Chile", o la decisión de no parsear `Quotations`) fueron tomadas conscientemente con la información disponible durante el desarrollo del proyecto, y podrían revisarse con nueva evidencia más adelante.

<hr>
<p align="center"><sub>Proyecto de práctica — pipeline de datos GDELT para investigación en Finanzas, Contabilidad, ESG e Inteligencia Artificial.</sub></p>
