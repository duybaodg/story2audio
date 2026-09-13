import io
import sys
import time

from gtts import gTTS


def synthesize(text: str, language: str) -> bytes:
    last_error = None
    for attempt in range(3):
        try:
            output = io.BytesIO()
            gTTS(text=text, lang=language, timeout=15).write_to_fp(output)
            return output.getvalue()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise last_error


if __name__ == "__main__":
    sys.stdout.buffer.write(synthesize(sys.stdin.read(), sys.argv[1]))
