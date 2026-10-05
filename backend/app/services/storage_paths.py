from pathlib import Path


def project_root() -> Path:
    cwd = Path.cwd()
    if cwd.name == "backend":
        return cwd.parent
    if (cwd / "backend").exists() and (cwd / "config").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate
