"""The reviewed, ordered Compose files belonging to one explicit installation root."""

from pathlib import Path, PurePosixPath


def configuration_names(root, installation):
    names = installation.get("config_files", ["compose.yaml"])
    if not isinstance(names, list) or not names:
        raise ValueError("unsafe_installation_compose_file")
    result = []
    for name in names:
        if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
            raise ValueError("unsafe_installation_compose_file")
        candidate = Path(name)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(root)
            except ValueError:
                raise ValueError("unsafe_installation_compose_file") from None
        path = PurePosixPath(candidate.as_posix())
        if path.is_absolute() or not path.parts or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError("unsafe_installation_compose_file")
        value = path.as_posix()
        if value in result:
            raise ValueError("duplicate_installation_compose_file")
        result.append(value)
    return tuple(result)


def configuration_files(root, installation):
    """Validate every path component before backup or freezing Docker's exact identity."""
    names = configuration_names(root, installation)
    for name in names:
        path = root
        for part in PurePosixPath(name).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("unsafe_installation_compose_file")
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("installation_compose_file_missing")
    return names
