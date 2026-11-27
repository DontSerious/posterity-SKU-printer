# notes.md (sku, review fix round 2026-11-26)
- S1, S2, S3 fixed in 995b3ba. S4 kept (test_label_fill_width.py: one test proves the hand module count equals the encoder, the other two use hand numbers only). S5: nothing to do.
- Spec b1 kept: hooks/pre-push also runs checks/tspl_capture.py (about 1 s, the audit's B5).
- No formatter is configured in this repo (pyproject has none), so none was run.
