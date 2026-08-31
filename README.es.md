# repuestos-radar

[![CI](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml)

> 🇬🇧 [Read this in English](README.md)

Inteligencia de mercado para talleres de reparación de celulares: sigue los precios de repuestos y
de celulares usados en revendedores locales verificados, guarda el historial en Postgres y
lo sirve en un dashboard que responde preguntas reales de precios.

## Qué es

repuestos-radar está hecho para un cliente real: un taller de reparación de celulares en Rosario,
Argentina. El taller compra módulos de pantalla, baterías y otros repuestos — y compra y vende
celulares usados — en un mercado donde los precios cambian todo el tiempo y presupuestar una
reparación implica revisar media docena de fuentes a mano.

Este proyecto automatiza eso: vigila los repuestos y equipos que le importan al taller, registra
los precios todos los días desde varias fuentes y convierte ese historial en respuestas — cuánto
sale hoy este módulo, quién lo tiene más barato, si esa oferta de un usado está por encima o por
debajo del mercado, y qué margen deja una reparación a los precios de hoy.

## Cómo funciona

1. **Ingesta diaria multi-fuente.** Un cron de GitHub Actions corre una vez por día y trae las
   publicaciones actuales de cada búsqueda seguida desde las tiendas online de los revendedores
   locales verificados del registro de fuentes.
2. **Historial de precios en Postgres.** Cada publicación se normaliza a un esquema común y se
   agrega a una base Postgres hosteada (Neon, plan gratuito), armando un historial de precios en
   el tiempo.
3. **Dashboard en Streamlit.** Un dashboard primero en español (con selector ES/EN) muestra precios
   actuales, historial y una calculadora de margen. Es público de solo lectura, salvo una página
   de administración protegida con contraseña donde el cliente gestiona la lista de seguimiento —
   las búsquedas seguidas viven en una tabla de la base, así que el cliente agrega o saca ítems
   sin tocar código.

## Fuentes de datos y política de confianza

Los precios valen lo que valen sus fuentes, así que cada fuente lleva metadatos de confianza:
dirección física, calificación y reseñas en Google e inscripción societaria, cuando se conocen.
Una fuente se agrega solo después de pasar una checklist de verificación documentada: tiene que
ser un comercio establecido con dirección verificable, precios públicos y stock consistente. El
registro vive en [`sources.yaml`](sources.yaml).

Fuentes actuales (comercios establecidos, de Rosario salvo que se indique otra ciudad):

| Fuente | Plataforma | Dirección | Cómo la leemos |
| --- | --- | --- | --- |
| Novocell | Wix | Av. Pellegrini 356 | Scraping respetuoso |
| Tienda Móvil | WooCommerce | Mendoza 1209 | Scraping respetuoso |
| Evophone | WooCommerce | Av. Pellegrini 4041 | Scraping respetuoso |
| Celuphone | WooCommerce | Santa Fe 4245 | Scraping respetuoso |
| Litoral Accesorios | WooCommerce | Mitre 1158 | Scraping respetuoso |
| MD Repuestos Originales | Tiendanube | Drysdale 5596, Carapachay — Vicente López (Bs. As.) | Scraping respetuoso |
| GoFix | Tiendanube | Av. Avelino Rolón 217 — CABA | Scraping respetuoso |
| One Store | Tiendanube | San Martín 1198 — Mendoza (ciudad) | Scraping respetuoso |

Las tiendas en Tiendanube no exponen una API JSON pública y sus rutas de búsqueda están
prohibidas por robots.txt, así que el adaptador recorre con respeto sus páginas de categoría
(JSON-LD de schema.org) una vez por corrida diaria en vez de buscar.

**¿Por qué no MercadoLibre?** Su API de búsqueda de publicaciones está restringida a partners
certificados (las credenciales comunes de aplicación y de usuario reciben 403), y sus páginas de
publicaciones redirigen los requests automatizados — incluso con user-agent honesto — a un muro de
verificación. Nuestra propia política de cortesía dice que los sitios que rechazan el acceso
automatizado se saltean, no se les busca la vuelta, así que no incorporamos los precios de
MercadoLibre.
Las credenciales de ML quedan en uso solo para la API de catálogo (normalización de nombres de
producto, en un hito posterior).

## Política de cortesía en el scraping

Las tiendas locales se scrapean con respeto, y esto no se negocia:

- **Una vez por día** — una sola corrida programada, nunca más que eso.
- **Se respeta robots.txt** — las rutas no permitidas no se tocan.
- **User-agent honesto** — los requests identifican al proyecto; no nos hacemos pasar por un
  navegador.
- **Backoff ante errores** — si una fuente falla, se reintenta con calma y después se saltea por
  ese día.
- **Los sitios que bloquean bots se saltean** — si un sitio deja claro que no quiere acceso
  automatizado, se lo saca de la rotación en vez de buscarle la vuelta.

## Arquitectura

Cada fuente es un adaptador autocontenido detrás de una interfaz común: el adaptador sabe cómo
descargar y parsear una fuente, y emite publicaciones en un único esquema normalizado — **ítem,
precio, condición, fuente, fecha**. El job de ingesta corre todos los adaptadores, junta las
publicaciones normalizadas y las escribe en Postgres. Todo lo que viene después (análisis,
dashboard, alertas) solo ve el esquema normalizado, así que agregar una fuente nunca toca el
resto del sistema.

```
Novocell (Wix) ─────┐
Tienda Móvil (Woo) ─┼──► adaptadores por fuente ──► publicaciones normalizadas ──► Postgres ──► dashboard
Evophone (Woo) ─────┤      (descarga + parseo)      (ítem, precio, condición,                   análisis
Celuphone (Woo) ────┘                                fuente, fecha)                             alertas
```

## Hoja de ruta

- **M0 — Andamiaje** *(este PR)*: estructura del proyecto, CI, documentación.
- **M1 — Ingesta**: adaptadores de las tiendas locales verificadas, esquema normalizado de
  publicaciones, escritura en Postgres.
- **M2 — Automatización diaria**: cron de GitHub Actions, manejo de errores, backoff, reportes de
  cada corrida.
- **M3 — Capa de análisis**: consultas sobre el historial, mejor precio y tendencias, cálculo de
  márgenes.
- **M4 — Dashboard + administración**: app en Streamlit (primero en español, selector ES/EN),
  vistas públicas de solo lectura, gestión de la lista de seguimiento protegida con contraseña.
- **M5 — Alertas y pronósticos**: alertas de baja de precio y pronósticos simples de tendencia.

## Entorno de desarrollo

Requiere Python 3.12 o superior.

```bash
git clone https://github.com/ZahirJacob/repuestos-radar.git
cd repuestos-radar
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Configurá las variables de entorno copiando la plantilla y completando los valores reales (nunca
se commitean — `.env` está en el gitignore):

```bash
cp .env.example .env
```

Corré los chequeos:

```bash
ruff check .
ruff format --check .
pytest
```

## Correr una ingesta

El runner de ingesta trae las publicaciones actuales de todas las fuentes verificadas para cada
búsqueda seguida activa, las etiqueta con el filtro de relevancia y las guarda como snapshots
diarios. Necesita `DATABASE_URL` en el entorno (o en `.env`) — una URL de Postgres o SQLite; las
tablas se crean automáticamente si no existen.

```bash
python -m repuestos_radar.ingest
```

El reporte de la corrida sale por stdout como líneas `clave=valor` fáciles de grepear — por
fuente: búsquedas consultadas, publicaciones traídas, productos malformados salteados, filas
insertadas vs ya guardadas ese día, el desglose de relevancia (`match` / `low_confidence` /
`reject`) y el mensaje de error si la fuente no estuvo disponible — más una línea de resumen. Una
fuente que falla nunca aborta la corrida: se reporta y las demás siguen. El código de salida es 0
cuando al menos una fuente funcionó (una corrida sin búsquedas activas es un no-op exitoso) y 1
cuando fallaron todas las fuentes o la corrida no pudo arrancar. El progreso se commitea después
de cada guardado por fuente/búsqueda y el almacenamiento es idempotente por día, así que volver a
correr después de una corrida interrumpida es seguro. Este es el job que corre dentro del workflow
de [automatización diaria](#automatización-diaria).

## Automatización diaria

Un workflow de GitHub Actions ([`ingest.yml`](.github/workflows/ingest.yml)) corre la ingesta una
vez por día a las 09:00 UTC (06:00 en Argentina) — exactamente una corrida programada por día, como
exige la política de cortesía de scraping. El job hace checkout del repo, instala las dependencias
fijadas con uv (`uv sync --locked`, contra el `uv.lock` commiteado) y corre
`python -m repuestos_radar.ingest`; el reporte de la corrida queda en el log del workflow. Un grupo de concurrencia evita que las corridas se superpongan, el job tiene un timeout
de 15 minutos y nunca se reintenta automáticamente.

Necesita el secret de repositorio `DATABASE_URL` (Settings → Secrets and variables → Actions) con
la cadena de conexión de Postgres. Hasta que el secret no esté configurado, las corridas abortan al
arrancar con `ingestion aborted (database error)` y una corrida en rojo — visible, inofensiva y se
arregla agregando el secret.

Para disparar una corrida a mano: Actions → "Daily ingestion" → "Run workflow", o
`gh workflow run ingest.yml`.

## Gestionar las búsquedas seguidas

Hasta que exista la página de administración del dashboard (M4), la lista de seguimiento se
gestiona con una pequeña CLI de desarrollo (mismo contrato de `DATABASE_URL` que el runner):

```bash
python -m repuestos_radar.tracked add "modulo samsung a34"
python -m repuestos_radar.tracked list
python -m repuestos_radar.tracked pause 3
python -m repuestos_radar.tracked resume 3
```

`add` con una búsqueda ya seguida te lo avisa en vez de fallar, y reactiva el ítem si estaba
pausado. Los ítems se pausan, no se borran: un ítem pausado conserva su historial de precios y
simplemente queda afuera de la ingesta diaria.
