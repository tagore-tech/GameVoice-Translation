import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import time

from translation.deep_translator_provider import DeepTranslatorProvider

p = DeepTranslatorProvider()

tests = [
    ("Enemy on the left", "en", "hi"),
    ("Dushman left side pe hai", "hi", "te"),
    ("Nannu cover cheyyi", "te", "ta"),
]

all_pass = True
for text, src, tgt in tests:
    t0 = time.perf_counter()
    r = p.translate(text, src, tgt)
    ms = (time.perf_counter() - t0) * 1000
    if r.status == "ok":
        print(f"PASS [{src}->{tgt}] {ms:.0f}ms")
        print(f"  source:     {text!r}")
        print(f"  translated: {r.translated_text!r}")
    else:
        print(f"FAIL [{src}->{tgt}]: {r.status} - {r.error_message}")
        all_pass = False

sys.exit(0 if all_pass else 1)
