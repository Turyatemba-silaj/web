from pathlib import Path

_models_dir = Path(__file__).resolve().parent
_app_dir = _models_dir.parent

for _models_path in (
    _models_dir / "hr.py",
    _models_dir / "operations.py",
    _models_dir / "finance.py",
    _app_dir / "models.py",
):
    exec(compile(_models_path.read_text(encoding="utf-8"), str(_models_path), "exec"), globals())

__all__ = [name for name in globals() if not name.startswith("_")]
