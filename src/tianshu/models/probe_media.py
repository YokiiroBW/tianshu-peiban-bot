"""Small deterministic synthetic fixtures; no user attachments or remote downloads."""

import base64
import io
import math
import struct
import wave

from PIL import Image, ImageDraw, ImageFont


def _image(code):
    image = Image.new("RGB", (512, 160), "white")
    ImageDraw.Draw(image).text((45, 30), code, fill="black", font=ImageFont.load_default(size=88))
    return image


def sample(kind, seed):
    code = str(1000 + int(seed[:8], 16) % 9000)
    output = io.BytesIO()
    if kind == "vision":
        _image(code).save(output, format="PNG")
        return "image/png", output.getvalue(), code
    if kind == "video":
        import av

        second = str(1000 + int(seed[8:16], 16) % 9000)
        if second == code:
            second = str(1000 + (int(second) - 999) % 9000)
        with av.open(output, mode="w", format="mp4") as container:
            stream = container.add_stream("libx264", rate=2)
            stream.width, stream.height, stream.pix_fmt = 512, 160, "yuv420p"
            for text in (code, second):
                for _ in range(4):
                    for packet in stream.encode(av.VideoFrame.from_image(_image(text))):
                        container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return "video/mp4", output.getvalue(), code + "," + second
    counts = [2 + int(seed[index : index + 2], 16) % 5 for index in (0, 2, 4)]
    rate = 16000
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        for count in counts:
            for _ in range(count):
                samples = (
                    int(12000 * math.sin(2 * math.pi * 660 * i / rate)) for i in range(rate // 4)
                )
                audio.writeframes(b"".join(struct.pack("<h", value) for value in samples))
                audio.writeframes(b"\0\0" * (rate // 4))
            audio.writeframes(b"\0\0" * rate)
    return "audio/wav", output.getvalue(), ",".join(map(str, counts))


def data_uri(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
