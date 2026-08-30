# repuestos-radar

[![CI](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml)

> 🇬🇧 [Read this in English](README.md)

Inteligencia de mercado para talleres de reparación de celulares: sigue los precios de repuestos y
de celulares usados en MercadoLibre y en revendedores locales, guarda el historial en Postgres y
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
   publicaciones actuales de cada búsqueda seguida — desde la API oficial de MercadoLibre
   (credenciales OAuth de aplicación) y desde tiendas online de revendedores locales verificados.
2. **Historial de precios en Postgres.** Cada publicación se normaliza a un esquema común y se
   agrega a una base Postgres hosteada (Neon/Supabase, plan gratuito), armando un historial de precios en
   el tiempo.
3. **Dashboard en Streamlit.** Un dashboard primero en español (con selector ES/EN) muestra precios
   actuales, historial y una calculadora de margen. Es público de solo lectura, salvo una página
   de administración protegida con contraseña donde el cliente gestiona la lista de seguimiento —
   las búsquedas seguidas viven en una tabla de la base, así que el cliente agrega o saca ítems
   sin tocar código.

## Fuentes de datos y política de confianza

Los precios valen lo que valen sus fuentes, así que cada fuente lleva metadatos de confianza:
dirección física, calificación en Google y — para vendedores de MercadoLibre — la reputación del
vendedor en la plataforma. Una fuente se agrega solo después de pasar una checklist de
verificación documentada: tiene que ser un comercio establecido con dirección verificable o buena
reputación en la plataforma, precios públicos y stock consistente.

Fuentes actuales:

| Fuente | Tipo | Cómo la leemos |
| --- | --- | --- |
| MercadoLibre | Marketplace (toda Argentina) | API oficial, credenciales OAuth de aplicación |
| Novocell (Rosario) | Tienda online de revendedor local (Wix) | Scraping respetuoso |
| Tienda Móvil (Rosario) | Tienda online de revendedor local (WooCommerce) | Scraping respetuoso |

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
dashboard, alertas) solo ve el esquema normalizado, así que agregar una fuente nunca toca el resto del sistema.

```
API MercadoLibre ──┐
Novocell (Wix) ────┼──► adaptadores por fuente ──► publicaciones normalizadas ──► Postgres ──► dashboard
Tienda Móvil ──────┘      (descarga + parseo)      (ítem, precio, condición,                   análisis
                                                    fuente, fecha)                             alertas
```

## Hoja de ruta

- **M0 — Andamiaje** *(este PR)*: estructura del proyecto, CI, documentación.
- **M1 — Ingesta**: adaptador de la API de MercadoLibre y de las tiendas locales, esquema
  normalizado de publicaciones, escritura en Postgres.
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
