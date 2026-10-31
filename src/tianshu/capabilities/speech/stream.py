"""Fish cumulative SSE alignments, including fragmented network frames."""
import base64,binascii,json
from ..contracts import Fault,require
MAX_AUDIO=64*1024*1024
async def fish_stream(response):
    audio, snapshots, lines, size = bytearray(), {}, [], 0

    def consume():
        if not lines:
            return
        data = "\n".join(lines)
        lines.clear()
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
            sequence = event["chunk_seq"]
            require(
                type(sequence) is int and 0 <= sequence < 10000,
                "speech_response_invalid",
                502,
            )
            audio.extend(base64.b64decode(event.get("audio_base64", ""), validate=True))
            require(len(audio) <= MAX_AUDIO, "speech_response_invalid", 502)
            alignment = event.get("alignment")
            if alignment:
                offset = float(event["chunk_audio_offset_sec"])
                # Each alignment is a replacement cumulative snapshot for this chunk.
                snapshots[sequence] = [
                    {
                        "text": item["text"],
                        "start": offset + float(item["start"]),
                        "end": offset + float(item["end"]),
                    }
                    for item in alignment["segments"]
                ]
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise Fault("speech_response_invalid", 502) from None

    # aiohttp's line reader preserves split TCP packets and rejects unbounded lines.
    async for raw in response.content:
        size += len(raw)
        require(size <= MAX_AUDIO * 2, "speech_response_invalid", 502)
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            consume()
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip())
    consume()
    return bytes(audio), [item for seq in sorted(snapshots) for item in snapshots[seq]]
