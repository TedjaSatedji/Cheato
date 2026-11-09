"""Probe the REAL model with the exact prompt cheat.py sends.

One API call, printed straight away. Answers the question a full e2e run can't
answer cheaply: does this endpoint return a bare letter, or prose we can't use?

    python probe_llm.py
"""
import os
import sys
import time

sys.path.insert(0, r"C:\Users\Tedja\Downloads\Cheat")
os.chdir(r"C:\Users\Tedja\Downloads\Cheat")
os.environ["CHEAT_ENV_FILE"] = "test-real-llm.env"

import cheat  # noqa: E402

QUESTION = "Prinsip kerja stack adalah ..."
OPTIONS = ["FIFO (First In, First Out)", "LIFO (Last In, First Out)",
           "akses acak ke setiap elemen", "penyisipan selalu terurut"]

materials = cheat.load_materials()
print(f"materials: {len(materials)} chars")
print(f"model: {cheat.MODEL} @ {cheat.BASE_URL}")

client = cheat.build_client()
t0 = time.time()
answer = cheat.get_answer_letter(client, materials, QUESTION, OPTIONS)
print(f"elapsed: {time.time() - t0:.1f}s")
print(f"parsed letter: {answer!r}   expected: 'b'")
print("VERDICT:", "OK" if answer == "b" else "PROBLEM")
