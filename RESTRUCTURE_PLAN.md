# Plan de reestructuración del proyecto Manga

## Situación actual

- **Raíz plana**: más de 15 archivos en la raíz (downloaders, GUIs, utilidades, config, JSONs).
- **Código duplicado**: `load_config()` y `save_metadata()` repetidos en cada downloader con pequeñas variaciones.
- **Archivos muy largos**: `tomosmanga_downloader.py` ~2300 líneas, `manga_downloader_gui.py` ~2600 líneas.
- **Acoplamiento**: `manga_downloader_gui.py` importa todos los downloaders, CBR y create_rar; cualquier cambio obliga a tocar un solo archivo enorme.
- **Rutas frágiles**: `script_dir`, `resources/debug`, `logos` usan `__file__`; al mover módulos hay que actualizar rutas.
- **Dos GUIs**: Tkinter (unificada) y PySide6 (solo TomosManga), sin un criterio claro de dónde vive cada cosa.

## Objetivos

1. Separar por responsabilidades: descarga, interfaz, utilidades, configuración.
2. Unificar configuración y metadata en un solo lugar.
3. Facilitar añadir nuevos sitios o nuevas GUIs sin tocar todo el proyecto.
4. Mantener compatibilidad con ejecución actual (scripts en raíz o entrada única).

---

## Estructura propuesta

```
Manga/
├── config.json
├── requirements.txt
├── README.md
├── .gitignore
├── run_gui.py                    # Entrada: GUI unificada (Tkinter)
├── run_tomosmanga_gui.py        # Entrada: GUI TomosManga (PySide6)
│
├── src/
│   ├── __init__.py
│   ├── config.py                 # load_config(), default_config, config_path
│   ├── metadata.py               # save_metadata() unificado por fuente (o por formato)
│   │
│   ├── downloaders/
│   │   ├── __init__.py           # Reexporta: MangaDownloader, TomosMangaDownloader, ...
│   │   ├── base.py               # (opcional) Clase base / mixins comunes
│   │   ├── manga_downloader.py
│   │   ├── mangatv_downloader.py
│   │   ├── tomosmanga_downloader.py
│   │   ├── lectorknight_downloader.py
│   │   ├── olympus_scan_downloader.py
│   │   └── zonatmo_downloader.py
│   │
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── main_gui.py           # Tkinter: manga_downloader_gui actual
│   │   └── tomosmanga_gui.py     # PySide6: tomosmanga_gui actual
│   │
│   └── utils/
│       ├── __init__.py
│       ├── cbr_generator.py
│       ├── create_rar.py
│       ├── delete_001_images.py
│       └── delete_images_gui.py
│
├── resources/
│   ├── debug/                    # HTML de debug (opcional, creado en runtime)
│   └── logos/                    # logos por sitio
│
└── data/                         # (opcional) JSONs de ejemplo/datos
    └── ejemplo_ordenamiento.json
```

Los scripts de CLI (`python manga_downloader.py`, etc.) pueden quedarse en la raíz como thin wrappers que importan desde `src.downloaders.*` y llaman a `main()`, o moverse a `scripts/` y ejecutarse con `python -m` desde la raíz.

---

## Cambios por área

### 1. Configuración (`src/config.py`)

- Una sola `load_config()` que lee `config.json` desde la raíz del proyecto (definir `PROJECT_ROOT` una vez, p. ej. junto a `config.py` o por variable de entorno).
- `default_config` único con todas las claves usadas por cualquier downloader (timeout, retry_attempts, delay_*, output_dir, selenium_*, parallel_*, etc.).
- Los downloaders dejan de definir su propio `load_config()` y usan `from src.config import load_config` (o `from ..config import load_config` si están en `src.downloaders`).

### 2. Metadata (`src/metadata.py`)

- Una función `save_metadata(source_id, manga_title, volumes, output_dir, **kwargs)` donde `source_id` es `'inventario_oculto'`, `'tomosmanga'`, `'olympus_scan'`, etc.
- Internamente un `if/elif` o un diccionario de funciones por `source_id` para el formato concreto de cada fuente (algunas tienen `manga_type`, `tomos_structure`, etc.). Así se mantiene la misma API que hoy pero en un solo módulo.
- Los downloaders pasan a usar `from src.metadata import save_metadata` (o ruta relativa).

### 3. Downloaders (`src/downloaders/`)

- Cada archivo actual se mueve a `src/downloaders/` sin cambiar nombre (o con nombre en minúsculas con guiones bajos si se prefiere consistencia).
- Imports internos: en lugar de `load_config` y `save_metadata` locales, importar desde `src.config` y `src.metadata`. Rutas de debug: usar una función helper, p. ej. `get_resource_path('debug', 'debug_html_tomosmanga.html')` que resuelva hacia `resources/debug/` desde la raíz del proyecto.
- `src/downloaders/__init__.py`: exportar todas las clases y `save_metadata` por sitio si se mantienen wrappers, o solo las clases y que la GUI importe `save_metadata` desde `src.metadata` con `source_id`.

### 4. GUIs (`src/gui/`)

- `main_gui.py`: contenido actual de `manga_downloader_gui.py`. Imports: `from src.downloaders import ...` y `from src.utils.cbr_generator import CBRGenerator`, `from src.utils.create_rar import ...`, `from src.config import load_config`, `from src.metadata import save_metadata` (usando `source_id`).
- `tomosmanga_gui.py`: contenido actual de `tomosmanga_gui.py`. Imports: `from src.downloaders.tomosmanga_downloader import TomosMangaDownloader`, `from src.config import load_config`, `from src.metadata import save_metadata` (con `source_id='tomosmanga'`).
- Rutas a logos: `get_resource_path('logos', 'logo_tomosmanga.png')` para que funcione desde `src/gui/`.

### 5. Utilidades (`src/utils/`)

- Mover `cbr_generator.py`, `create_rar.py`, `delete_001_images.py`, `delete_images_gui.py` a `src/utils/`.
- Si algo usa `__file__` para encontrar datos, sustituir por una función común que resuelva respecto a la raíz del proyecto o a `resources/`.

### 6. Puntos de entrada (raíz)

- `run_gui.py`: algo como `from src.gui.main_gui import main; main()` (o lanzar la app Tkinter).
- `run_tomosmanga_gui.py`: `from src.gui.tomosmanga_gui import main; main()`.
- Opcional: `run_manga_cli.py`, `run_tomosmanga_cli.py` que importen el downloader correspondiente y llamen a su `main()` para no romper costumbre de `python manga_downloader.py`.

---

## Rutas y recursos

- Definir **raíz del proyecto** en un solo sitio, por ejemplo en `src/config.py`:

  ```python
  import os
  PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  ```

- Función común para recursos:

  ```python
  def get_resource_path(*parts):
      return os.path.join(PROJECT_ROOT, 'resources', *parts)
  ```

- `config.json`: seguir en la raíz; `load_config()` lo resuelve como `os.path.join(PROJECT_ROOT, 'config.json')`.
- Debug HTML y logos: bajo `resources/debug/` y `resources/logos/`; todos los módulos usan `get_resource_path()`.

---

## Orden de migración sugerido (por fases)

1. **Fase 1 – Base**
   - Crear `src/`, `src/config.py` (unificar `load_config` y `PROJECT_ROOT`), `src/metadata.py` (unificar `save_metadata` por `source_id`), `src/utils/` con helpers de rutas si hace falta.
   - Crear `resources/` y `resources/logos/` (mover o copiar logos).
   - No mover aún los downloaders; solo hacer que sigan pudiendo ejecutarse desde la raíz pero que importen `load_config` desde `src.config` (y opcionalmente `save_metadata` desde `src.metadata`). Para eso habría que añadir al inicio de cada downloader algo como `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` si se prefiere no instalar el paquete, o instalar el proyecto en desarrollo con `pip install -e .`.

2. **Fase 2 – Paquete instalable (recomendado)**
   - Añadir en la raíz `pyproject.toml` (o `setup.py`) para que el proyecto sea un paquete con nombre tipo `manga_downloader`. Entonces desde cualquier script se puede hacer `from manga_downloader.config import load_config` y `from manga_downloader.downloaders.tomosmanga_downloader import TomosMangaDownloader`.
   - Estructura de paquete podría ser entonces `manga_downloader/` en lugar de `src/` (o mantener `src/` y que el paquete se llame igual). Ejemplo con `src`:

   ```
   Manga/
   ├── pyproject.toml
   ├── src/
   │   └── manga_downloader/
   │       ├── __init__.py
   │       ├── config.py
   │       ├── metadata.py
   │       ├── downloaders/
   │       ├── gui/
   │       └── utils/
   ```

   Así los imports son siempre `from manga_downloader.... import ...` y no hay que tocar `sys.path`.

3. **Fase 3 – Mover downloaders**
   - Mover cada `*_downloader.py` a `src/downloaders/` (o `src/manga_downloader/downloaders/`), actualizar imports a `config` y `metadata`, y sustituir `script_dir` + rutas a debug por `get_resource_path('debug', ...)`.
   - Añadir `src/downloaders/__init__.py` que reexporte clases y, si aplica, `save_metadata` por módulo.

4. **Fase 4 – Mover GUIs**
   - Mover `manga_downloader_gui.py` → `src/gui/main_gui.py` y `tomosmanga_gui.py` → `src/gui/tomosmanga_gui.py`, actualizando todos los imports a `manga_downloader.downloaders.*`, `manga_downloader.utils.*`, `manga_downloader.config`, `manga_downloader.metadata`.
   - Crear `run_gui.py` y `run_tomosmanga_gui.py` en la raíz que importen y ejecuten la función `main()` de cada GUI.

5. **Fase 5 – Utilidades y limpieza**
   - Mover `cbr_generator.py`, `create_rar.py`, `delete_001_images.py`, `delete_images_gui.py` a `src/utils/` y actualizar imports en GUIs y entre utilidades.
   - Dejar en la raíz solo: `config.json`, `requirements.txt`, `README.md`, `.gitignore`, `run_*.py`, y opcionalmente scripts CLI del tipo `manga_downloader.py` que sean un one-liner que importa y llama `main()`.

---

## División futura de archivos muy grandes

- **tomosmanga_downloader.py**: separar por responsabilidades, por ejemplo:
  - `tomosmanga_downloader.py`: clase principal, `get_page`, `parse_volumes`, `get_manga_title`, orquestación.
  - `ouo_bypass.py` (o dentro de `tomosmanga/`): lógica de bypass ouo.io y Selenium.
  - `fireload.py`: obtención de URLs de Fireload y descarga con Selenium.
  Así el archivo principal baja de ~2300 a unos cientos de líneas y cada módulo es más legible y testeable.

- **manga_downloader_gui.py** (main_gui.py): dividir en:
  - Ventana principal y pestañas.
  - Un módulo por “flujo” (descarga por sitio, generación CBR, creación RAR) o por tipo de widget (lista de tomos, opciones, log).
  - Callbacks y workers en módulos separados si crece la complejidad.

Esto puede hacerse en una fase 6 una vez la reestructuración de carpetas esté estable.

---

## Resumen de beneficios

| Antes | Después |
|-------|--------|
| Config y metadata en cada downloader | Un solo `config.py` y `metadata.py` |
| Rutas con `__file__` en cada archivo | `PROJECT_ROOT` y `get_resource_path()` centralizados |
| Raíz con 15+ archivos | Raíz con config, README y 2–3 scripts de entrada |
| GUIs y downloaders en la misma “capa” | Capa `downloaders/`, capa `gui/`, capa `utils/` |
| Añadir sitio = tocar varios sitios del código | Añadir sitio = nuevo módulo en `downloaders/` + entrada en `metadata` y en la GUI que corresponda |

Si quieres, el siguiente paso puede ser aplicar la Fase 1 (solo `config.py`, `metadata.py` y rutas) sin mover aún ningún downloader, para validar el enfoque con el mínimo cambio posible.
