import os
import re
import json
from manga_downloader.paths import get_project_root

def save_metadata(source_id, manga_title, volumes, output_dir, manga_type='manga', tomos_structure=None):
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(get_project_root(), output_dir)
    if source_id == 'olympus_scan':
        metadata = {
            'manhwa_title': manga_title,
            'url': '',
            'volumes': []
        }
        for volume in volumes:
            volume_data = {
                'name': volume['name'],
                'tomo_number': re.search(r'(\d+)', volume['name']).group(1) if re.search(r'(\d+)', volume['name']) else "1",
                'chapters': []
            }
            for chapter in volume['chapters']:
                volume_data['chapters'].append({'name': chapter['name'], 'url': chapter['url']})
            metadata['volumes'].append(volume_data)
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', manga_title)
        manga_dir = os.path.join(output_dir, safe_title)
        os.makedirs(manga_dir, exist_ok=True)
        metadata_path = os.path.join(manga_dir, 'manhwa_metadata.json')
        title_path = os.path.join(manga_dir, 'manhwa_title.txt')
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        with open(title_path, 'w', encoding='utf-8') as f:
            f.write(manga_title)
        return
    if source_id in ('mangatv', 'zonatmo'):
        if manga_type == 'manhwa':
            metadata = {'manhwa_title': manga_title, 'url': '', 'volumes': []}
        else:
            metadata = {'manga_title': manga_title, 'url': '', 'volumes': []}
        metadata['_source_type'] = source_id
        metadata['_manga_type'] = manga_type
        if tomos_structure and manga_type == 'manga':
            metadata['_tomos_structure'] = tomos_structure
        for volume in volumes:
            volume_data = {
                'name': volume['name'],
                'tomo_number': re.search(r'(\d+)', volume['name']).group(1) if re.search(r'(\d+)', volume['name']) else "1",
                'chapters': []
            }
            for chapter in volume['chapters']:
                volume_data['chapters'].append({'name': chapter['name'], 'url': chapter['url']})
            metadata['volumes'].append(volume_data)
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', manga_title)
        manga_dir = os.path.join(output_dir, safe_title)
        os.makedirs(manga_dir, exist_ok=True)
        if manga_type == 'manhwa':
            metadata_path = os.path.join(manga_dir, 'manhwa_metadata.json')
        else:
            metadata_path = os.path.join(manga_dir, 'manga_metadata.json')
        title_path = os.path.join(manga_dir, 'manga_title.txt')
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        with open(title_path, 'w', encoding='utf-8') as f:
            f.write(manga_title)
        return
    metadata = {'manga_title': manga_title, 'url': '', 'volumes': []}
    if source_id == 'tomosmanga':
        metadata['_source_type'] = 'tomosmanga'
    if source_id == 'lectorknight':
        metadata['_source_type'] = 'lectorknight'
    for volume in volumes:
        if 'chapters' in volume:
            volume_data = {
                'name': volume['name'],
                'tomo_number': re.search(r'(\d+)', volume['name']).group(1) if re.search(r'(\d+)', volume['name']) else "1",
                'chapters': [{'name': ch['name'], 'url': ch['url']} for ch in volume['chapters']]
            }
            metadata['volumes'].append(volume_data)
        else:
            volume_data = {
                'name': volume['name'],
                'tomo_number': re.search(r'(\d+)', volume['name']).group(1) if re.search(r'(\d+)', volume['name']) else "1",
                'chapters': [{'name': volume['name'], 'url': volume.get('url', '')}]
            }
            metadata['volumes'].append(volume_data)
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', manga_title)
    manga_dir = os.path.join(output_dir, safe_title)
    os.makedirs(manga_dir, exist_ok=True)
    metadata_path = os.path.join(manga_dir, 'manga_metadata.json')
    title_path = os.path.join(manga_dir, 'manga_title.txt')
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    with open(title_path, 'w', encoding='utf-8') as f:
        f.write(manga_title)
