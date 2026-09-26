import importlib.util
import os


def load_skill(name: str, skill_dir: str, entrypoint: str, factory: str, config: dict):
    """Import <skill_dir>/<entrypoint> under a unique module name and call <factory>(config).

    Raises ImportError/AttributeError/anything the module raises — the caller
    (SkillManager) isolates failures so one broken skill cannot take down Ari.
    """
    path = os.path.join(skill_dir, entrypoint)
    spec = importlib.util.spec_from_file_location(f"ari_skill_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load skill module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build = getattr(module, factory)
    return build(config)
