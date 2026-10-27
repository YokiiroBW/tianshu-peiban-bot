"""One Python environment over the existing Platform OS and four mature engines."""

import argparse
import json
from pathlib import Path
import re
import subprocess


COMPONENTS = ("platform", "vector", "loki", "prometheus", "grafana")
PIN = re.compile(r"(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
NAMED_DIGEST = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[a-f0-9]{64}\Z")
NAMED_TAG = re.compile(r"[a-z0-9][a-z0-9._:/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def validate(release):
    images = release.get("images", {})
    if set(images) != set(COMPONENTS):
        raise ValueError("release_image_set_invalid")
    if any(
        not isinstance(value, str) or not PIN.fullmatch(value)
        for value in images.values()
    ):
        raise ValueError("immutable_image_pin_required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", release.get("version", "")):
        raise ValueError("release_version_invalid")


def base_references(release):
    """Keep config IDs as expected identities, never as a Dockerfile repository."""
    validate(release)
    explicit = release.get("base_references", {})
    if not isinstance(explicit, dict) or set(explicit) - set(COMPONENTS):
        raise ValueError("base_reference_set_invalid")
    result = {}
    for name, image in release["images"].items():
        if IMAGE_ID.fullmatch(image):
            reference = explicit.get(name)
            if (
                not isinstance(reference, str)
                or IMAGE_ID.fullmatch(reference)
                or not (
                    NAMED_TAG.fullmatch(reference) or NAMED_DIGEST.fullmatch(reference)
                )
            ):
                raise ValueError("local_image_named_reference_required")
        else:
            reference = explicit.get(name, image)
            if reference != image or not NAMED_DIGEST.fullmatch(reference):
                raise ValueError("registry_digest_reference_must_be_preserved")
        result[name] = reference
    return result


def verify_local_bases(release, *, docker="docker"):
    """Read local daemon identities only; never pull, tag, run or build an image."""
    references = base_references(release)
    checked = {}
    template = (
        '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'
    )
    for name, reference in references.items():
        metadata = json.loads(
            subprocess.check_output(
                [docker, "image", "inspect", "--format", template, reference],
                text=True,
            )
        )
        actual = metadata.get("id")
        if not isinstance(actual, str) or not IMAGE_ID.fullmatch(actual):
            raise ValueError("local_base_image_identity_invalid")
        expected = release["images"][name]
        if IMAGE_ID.fullmatch(expected) and actual != expected:
            raise ValueError("local_base_image_identity_mismatch")
        if metadata.get("os") != "linux" or metadata.get("architecture") != "amd64":
            raise ValueError("local_base_image_platform_mismatch")
        checked[name] = {
            "reference": reference,
            "id": actual,
            "os": "linux",
            "architecture": "amd64",
        }
    return {"status": "local_base_identities_verified_not_built", "bases": checked}


def install_comic_aliases(media_directory="/media"):
    """Build-time aliases keep existing comic ledger paths on the private IPC."""
    root = Path(media_directory)
    if root.is_symlink() or root.exists() and not root.is_dir():
        raise ValueError("comic_alias_parent_invalid")
    aliases = {
        root / "ComicInputs": "/run/tianshu/comics/inputs",
        root / "ComicPublisher": "/run/tianshu/comics/publisher",
    }
    for path, target in aliases.items():
        if path.is_symlink():
            if path.readlink().as_posix() != target:
                raise ValueError("comic_alias_conflict")
        elif path.exists():
            raise ValueError("comic_alias_conflict")
    root.mkdir(parents=True, exist_ok=True)
    for path, target in aliases.items():
        if not path.is_symlink():
            path.symlink_to(target, target_is_directory=True)


def dockerfile(release):
    images = base_references(release)
    # Docker 24's bundled frontend handles every instruction below. An external
    # syntax image would add an unpinned pull to the otherwise offline build.
    lines = []
    lines.extend(
        f"FROM --platform=linux/amd64 {images[name]} AS source-{name}"
        for name in COMPONENTS[1:]
    )
    lines.extend(
        [
            f"FROM --platform=linux/amd64 {images['platform']} AS runtime",
            "USER 0:10001",
            "COPY wheels/ /opt/tianshu/distribution/wheels/",
            "COPY runtime.lock dependencies.json /opt/tianshu/distribution/",
            "WORKDIR /opt/tianshu/distribution",
            # Recreate rather than overlay the inherited Platform venv: an unchanged
            # product version must not cause pip to retain its older installed code.
            "RUN rm -rf /opt/tianshu-venv && /usr/local/bin/python -m venv /opt/tianshu-venv \\",
            "    && /opt/tianshu-venv/bin/python -m pip install --no-cache-dir --no-index --find-links=./wheels --only-binary=:all: --require-hashes -r runtime.lock \\",
            "    && /opt/tianshu-venv/bin/python -m pip check \\",
            '    && /opt/tianshu-venv/bin/python -c "import services.platform, services.platform.media.comics.plugin, tianshu_companion, tianshu_nonebot.onebot, tianshu_memory, tianshu_gateway, tianshu_storage"',
            "COPY --from=source-vector /usr/bin/vector /usr/local/bin/vector",
            "COPY --from=source-loki /usr/bin/loki /usr/local/bin/loki",
            "COPY --from=source-prometheus /bin/prometheus /bin/promtool /usr/local/bin/",
            "COPY --from=source-grafana /usr/share/grafana/ /usr/share/grafana/",
            "COPY --from=source-grafana /etc/grafana/ /etc/grafana/",
            "COPY --from=source-grafana /run.sh /run.sh",
            'ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH="/opt/tianshu/application" PATH="/opt/tianshu-venv/bin:/usr/share/grafana/bin:$PATH"',
            "RUN /usr/local/bin/vector --version && /usr/local/bin/loki --version && /usr/local/bin/prometheus --version && grafana --version",
            "COPY deploy/ /opt/tianshu/application/deploy/",
            "COPY release.json /opt/tianshu/distribution/",
            f'LABEL org.opencontainers.image.title="Tianshu" org.opencontainers.image.version="{release["version"]}"',
            'RUN /usr/local/bin/python -c "from deploy.single_container.image import install_comic_aliases; install_comic_aliases()"',
            "RUN mkdir -p /run/tianshu /data /config && chmod 0755 /run/tianshu",
            "WORKDIR /opt/tianshu/application",
            "STOPSIGNAL SIGTERM",
            'HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["/opt/tianshu-venv/bin/python", "-m", "deploy.single_container.health", "--state", "/run/tianshu/runtime.json"]',
            'ENTRYPOINT ["/opt/tianshu-venv/bin/python", "-m", "deploy.single_container.runtime"]',
            'CMD ["--config", "/config/runtime.json"]',
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify local base references without changing Docker state"
    )
    parser.add_argument("--verify-local-bases", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args(argv)
    release = json.loads(args.verify_local_bases.read_bytes())
    print(json.dumps(verify_local_bases(release, docker=args.docker)))


if __name__ == "__main__":
    main()
