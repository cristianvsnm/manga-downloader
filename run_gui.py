import sys
import os
if sys.platform == "win32":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false"
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("manga.downloader.gui")
    except Exception:
        pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from manga_downloader.gui.main_gui import main
if __name__ == '__main__':
    main()
