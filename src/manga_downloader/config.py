import os
import json
from manga_downloader.paths import get_config_path

def load_config():
    config_path = get_config_path()
    default_config = {
        "timeout": 60,
        "retry_attempts": 5,
        "retry_delay": 2,
        "delay_between_images": 0.1,
        "delay_between_chapters": 0.5,
        "delay_between_volumes": 3,
        "min_file_size": 0,
        "output_dir": "downloads",
        "selenium_wait_time": 30,
        "selenium_extra_wait": 3,
        "parallel_tomos": 3,
        "parallel_chapters": 2,
        "parallel_images": 8,
        "retry_failed_images": 5,
        "force_redownload": False
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                default_config.update(user_config)
        except Exception:
            pass
    return default_config
