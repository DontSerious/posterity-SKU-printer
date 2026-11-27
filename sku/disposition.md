# Disposition table: sku-barcode-printer (2026-11-26)

Branches: sku (tests/trim-1126, default branch main). Fix commit: 995b3ba. The Standards and Spec reviews for desk-panel and sku-barcode-printer are one document each (review-desk-*.md); only the sku findings are here.

Owner ruling, 2026-11-26: "全部修" (fix every Standards judgement item). No row is `won't fix`. Not findings, so no row: Spec (sku) reviewer's check that ocr.py:558 correct_ocr=True turns test_pasted_text_is_not_homoglyph_corrected red (confirmation; the branch's 13 mutants in mutants-new-tests.txt all show RED), and Standards S5 ("test_learned.py fixture: no problem found").

Rows are the findings of the Standards and Spec reviews. A review line the reviewer itself marked OK (no defect) is not a finding and has no row.

| axis | finding | disposition | evidence |
|---|---|---|---|
| Standards (sku) | S1 tests/test_parse_text.py header: README.md:220 listed after 245, the index is out of order | fix 995b3ba | git show 995b3ba tests/test_parse_text.py: 245 and 220 swapped into line order; pytest 66 passed as before (commit message) |
| Standards (sku) | S2 README "这一节放在文件末尾是故意的" no longer true, the pre-push section follows it | fix 995b3ba | git show 995b3ba README.md: note now "这一节和它后面的节放在被引用的段落之后是故意的 ... 新的一节加在文件末尾" |
| Standards (sku) | S3 README.md:292 "合起来不到两秒" has no source | fix 995b3ba | git show 995b3ba README.md: "2026-11-26 在 WSL 上量：机器负载 20 以上时 3.5～4.4 秒，空闲时约 2 秒" |
| Standards (sku) | S4 test_label_fill_width.py: first case couples the hand-counted module widths to label.code128_modules | ticket #4 | notes.md (sku): "S4 kept ... one test proves the hand module count equals the encoder, the other two use hand numbers only" |
| Spec (sku) | (b)1 hooks/pre-push also runs checks/tspl_capture.py, audit suggested wrapping it in pytest | ticket #4 | notes.md (sku) "Spec b1 kept: hooks/pre-push also runs checks/tspl_capture.py (about 1 s, the audit's B5)"; desk notes.md: wrapping it "would add a test that only shells out" |
| Spec (sku) | (c)5 README.md:269 "这一节放在文件末尾是故意的" false after the new "推送前检查" section | fix 995b3ba | same change as S2 |

## Unclear

None.
