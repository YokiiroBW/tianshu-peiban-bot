from dataclasses import replace
import io

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.files import CHUNK_SIZE, FileStore


def test_streaming_ingest_and_range_read_use_bounded_working_buffers(tmp_path):
    class BoundedSource(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= CHUNK_SIZE
            return super().read(size)

    store = FileStore(tmp_path)
    payload = b"0123456789" * 300000
    reference = store.put_stream(BoundedSource(payload), media_type="video/mp4")
    assert (
        b"".join(store.iter_bytes(reference, start=17, length=57, chunk_size=19)) == payload[17:74]
    )
    assert reference.size_bytes == len(payload)


async def test_async_upload_has_same_content_identity_and_no_partial_file(tmp_path):
    store = FileStore(tmp_path)

    async def chunks():
        yield b"first"
        yield b"second"

    streamed = await store.put_async(chunks(), media_type="audio/wav")
    assert streamed.sha256 == store.put_bytes(b"firstsecond").sha256
    assert not list((tmp_path / ".incoming").glob("*.partial"))


def test_local_probe_measurements_preserve_content_and_actual_intake_proof(tmp_path):
    store = FileStore(tmp_path)
    raw = b"actual local media"
    original = store.put_bytes(raw, media_type="video/mp4")
    with store.inspection_path(original) as path:
        assert path.is_absolute() and path.is_relative_to(tmp_path)
        assert path.read_bytes() == raw
        described = store.describe_intake(original, duration_ms=8000, width=640, height=360)
    assert described is not original
    assert store.was_written(described)
    assert replace(described, duration_ms=None, width=None, height=None) == original
    assert store.read_bytes(described) == raw
    assert store.describe_intake(described, duration_ms=4000).width == 640
    assert described.duration_ms == 8000


@pytest.mark.parametrize("kind", ["reconstructed", "external_url", "source", "other_store"])
def test_probe_ports_reject_asserted_or_foreign_intake_references(tmp_path, kind):
    store = FileStore(tmp_path / "owned")
    original = store.put_bytes(b"actual local media", media_type="video/mp4")
    untrusted = {
        "reconstructed": lambda: replace(original),
        "external_url": lambda: replace(original, uri="https://example.invalid/private.mp4"),
        "source": lambda: replace(original, media_id="source:asserted:1"),
        "other_store": lambda: FileStore(tmp_path / "foreign").put_bytes(b"actual local media"),
    }[kind]()
    assert not store.was_written(untrusted)
    with pytest.raises(AppError, match="media_intake_not_owned"):
        with store.inspection_path(untrusted):
            pytest.fail("an asserted intake must not expose a filesystem path")
    with pytest.raises(AppError, match="media_intake_not_owned"):
        store.describe_intake(untrusted, duration_ms=8000)


@pytest.mark.parametrize(
    "measurements",
    [
        {"duration_ms": -1},
        {"duration_ms": True},
        {"duration_ms": 1.25},
        {"width": 0},
        {"width": False},
        {"height": -1},
    ],
)
def test_probe_measurements_require_real_nonnegative_duration_and_positive_dimensions(
    tmp_path, measurements
):
    store = FileStore(tmp_path)
    original = store.put_bytes(b"actual local media")
    with pytest.raises(AppError, match="invalid_media_measurement"):
        store.describe_intake(original, **measurements)
    assert original.duration_ms is original.width is original.height is None


def test_probe_ports_verify_actual_content_before_exposing_a_path_or_attesting_metadata(tmp_path):
    store = FileStore(tmp_path)
    original = store.put_bytes(b"original")
    (tmp_path / original.sha256[:2] / original.sha256).write_bytes(b"tampered")
    with pytest.raises(AppError, match="file_content_mismatch"):
        with store.inspection_path(original):
            pytest.fail("a mutated file must not reach the local inspector")
    with pytest.raises(AppError, match="file_content_mismatch"):
        store.describe_intake(original, duration_ms=8000)
