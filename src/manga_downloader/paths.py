import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def get_project_root():
    return _PROJECT_ROOT

def get_resource_path(*parts):
    return os.path.join(_PROJECT_ROOT, 'resources', *parts)

def get_config_path():
    return os.path.join(_PROJECT_ROOT, 'config.json')

PROJECT_ROOT = _PROJECT_ROOT
