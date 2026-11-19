# Cómo ejecutar el proyecto (reestructurado)

## Opción A: Sin instalar el paquete (desde la raíz del proyecto)

Desde la carpeta `Manga` (raíz del proyecto):

**GUI (PySide6 – todos los sitios):**
```bash
python run_gui.py
```

**CLI Inventario Oculto:**
```bash
python run_manga_cli.py
```

**CLI TomosManga:**
```bash
python run_tomosmanga_cli.py
```

**Utilidades (CBR, eliminar imágenes, etc.):**  
Siguen en `src/manga_downloader/utils/`. Para usarlas como script:

```bash
python -c "import sys; sys.path.insert(0, 'src'); from manga_downloader.utils.delete_001_images import delete_001_images; delete_001_images('downloads/ruta_manga')"
```

O instalar el paquete (Opción B) y ejecutar los entry points.

---

## Opción B: Instalar el paquete en modo editable

En la raíz del proyecto (`Manga`):

```bash
pip install -e .
```

Luego puedes ejecutar:

**GUI:**
```bash
python run_gui.py
```
o
```bash
manga-downloader-gui
```

**CLI (importando desde el paquete):**
```bash
python -c "from manga_downloader.downloaders.manga_downloader import main; main()"
python -c "from manga_downloader.downloaders.tomosmanga_downloader import main; main()"
```

---

## Estructura de carpetas

- `config.json` en la raíz (se lee desde ahí).
- `resources/logos/`: logos por sitio (inventario_oculto, olympus_scan, etc.).
- `resources/debug/`: HTML de depuración (se crea al usar opciones de debug).
- `downloads/`: carpeta de descargas por defecto.

Si no existe `resources/logos/`, créala y coloca ahí los logos que use la GUI.
