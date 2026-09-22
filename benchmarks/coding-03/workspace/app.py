import yaml


def load_settings(path="settings.yaml"):
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    return {
        "retries": data["retries"],
        "mode": data["mode"],
        "timeout": data["timeout"],
    }
