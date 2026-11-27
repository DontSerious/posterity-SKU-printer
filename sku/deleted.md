# sku-barcode-printer, branch tests/trim-1126
Nothing deleted, merged or rewritten: the audit marks all five files KEEP. Added 27 tests: tests/test_label_fill_width.py (6), tests/test_learned.py (18 with parametrization), tests/test_parse_text.py (3: 2 parametrized paste cases + 1 contrast case). 13 mutants of label.py / learned.py / ocr.py, each caught (mutants-new-tests.txt).
per-test diff: baseline 39 tests, all PASSED; after 66, all PASSED; every baseline id has the same result.
