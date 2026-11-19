import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from manga_downloader.downloaders.manga_downloader import main
if __name__ == '__main__':
    main()
