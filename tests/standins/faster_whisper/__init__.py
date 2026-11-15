"""Stand-in for faster-whisper in the tests (on PYTHONPATH only for the test's own pipeline process): a made-up
transcript at once, no model download. RH_TEST_WHISPER_FAIL=1 makes the transcript step fail."""
import os


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _Info:
    language = "en"


class WhisperModel:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio, **k):
        if os.environ.get("RH_TEST_WHISPER_FAIL"):
            raise RuntimeError("test stand-in: transcription failed (RH_TEST_WHISPER_FAIL)")
        dur = max(1.0, len(audio) / 16000)
        segs = [_Seg(t, min(t + 1.5, dur), f"Test line at {t:.0f} seconds.") for t in range(0, int(dur), 2)]
        return iter(segs), _Info()
