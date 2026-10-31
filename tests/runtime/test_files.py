import io

from tianshu.runtime.files import CHUNK_SIZE, FileStore


def test_streaming_ingest_and_range_read_use_bounded_working_buffers(tmp_path):
    class BoundedSource(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= CHUNK_SIZE
            return super().read(size)

    store = FileStore(tmp_path)
    payload = b"0123456789"*300000
    reference = store.put_stream(BoundedSource(payload), media_type="video/mp4")
    assert b"".join(store.iter_bytes(reference, start=17, length=57, chunk_size=19)) == payload[17:74]
    assert reference.size_bytes == len(payload)


async def test_async_upload_has_same_content_identity_and_no_partial_file(tmp_path):
    store = FileStore(tmp_path)

    async def chunks():
        yield b"first"
        yield b"second"

    streamed = await store.put_async(chunks(), media_type="audio/wav")
    assert streamed.sha256 == store.put_bytes(b"firstsecond").sha256
    assert not list((tmp_path/".incoming").glob("*.partial"))
