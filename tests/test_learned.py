"""learned.py against README.md:218-236 (会自己纠错): what is remembered, when, and what it may do on its own.

The store is a file; every test points STORE_PATH at tmp_path and drops the in-memory cache, so learned.json in the
repository is never read or written. Expected values are the README rules:

    SKU 字典     打印成功要攒够 2 次；你亲手敲进去的立刻算数
    错 -> 对     你把 X 改成 Y 时记下，见 X 直接改成 Y
    模糊匹配     只认形近（O/0 I/1 S/5 B/8 Z/2 G/6），段结构完全一致、候选唯一，只给建议
    自愈         你把 X 改成 Y 时，X 如果在字典里就地删掉
    黑名单       记下的串被认出来，下次照样列出来
"""

import json

import pytest

import learned


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = tmp_path / "learned.json"
    monkeypatch.setattr(learned, "STORE_PATH", str(path))
    learned.reload_from_disk()
    yield path
    learned.reload_from_disk()


def test_one_successful_print_is_not_enough_two_are():
    learned.record_sku("PHC-ELE-BK")
    assert learned.known("PHC-ELE-BK") is None
    learned.record_sku("PHC-ELE-BK")
    assert learned.known("PHC-ELE-BK") == "PHC-ELE-BK"


def test_a_sku_typed_by_hand_counts_at_once():
    learned.record_sku("PHC-ELE-BK", strong=True)
    assert learned.known("PHC-ELE-BK") == "PHC-ELE-BK"


def test_known_puts_the_case_back_to_the_dictionary_spelling():
    learned.record_sku("UK4-SS-mn-GL-AU-BK", strong=True)
    assert learned.known("UK4-SS-MN-GL-AU-BK") == "UK4-SS-mn-GL-AU-BK"


def test_a_correction_is_applied_exactly_and_the_right_answer_enters_the_dictionary():
    learned.record_fix("PHC-ELE-8K", "PHC-ELE-BK")
    assert learned.fix("PHC-ELE-8K") == "PHC-ELE-BK"
    assert learned.fix("PHC-ELE-8L") is None
    assert learned.known("PHC-ELE-BK") == "PHC-ELE-BK"


def test_a_wrong_spelling_in_the_dictionary_is_removed_when_the_person_corrects_it():
    learned.record_sku("PHC-ELE-8K", strong=True)
    learned.record_fix("PHC-ELE-8K", "PHC-ELE-BK")
    assert learned.known("PHC-ELE-8K") is None
    assert learned.suggest("PHC-ELE-8L") is None


def test_a_lookalike_gets_a_suggestion_but_nothing_is_changed():
    learned.record_sku("PHC-ELE-BK", strong=True)
    assert learned.suggest("PHC-ELE-8K") == "PHC-ELE-BK"
    assert learned.fix("PHC-ELE-8K") is None
    assert learned.known("PHC-ELE-8K") is None


def test_two_equally_close_candidates_give_no_suggestion():
    learned.record_sku("PHC-0-X", strong=True)
    learned.record_sku("PHC-O-X", strong=True)
    assert learned.suggest("PHC-Q-X") is None
    learned.forget("skus", "PHC-0-X")
    assert learned.suggest("PHC-Q-X") == "PHC-O-X"


@pytest.mark.parametrize("seen", ["PHC-ELE-BL", "PHC-ELE-BKK", "PHC-ELE-8KK", "PHC-ELE-8", "PHC-ELE", "PHC-ELE-BK"])
def test_only_lookalike_letters_of_the_same_length_are_suggested(seen):
    learned.record_sku("PHC-ELE-BK", strong=True)
    assert learned.suggest(seen) is None


def test_two_real_models_that_differ_by_one_ordinary_letter_never_lean_on_each_other():
    learned.record_sku("PHC-ELE-WLA", strong=True)
    assert learned.suggest("PHC-ELE-WLB") is None


def test_a_sku_that_has_not_been_printed_twice_is_not_a_suggestion_target():
    learned.record_sku("PHC-ELE-BK")
    assert learned.suggest("PHC-ELE-8K") is None


def test_a_junk_verdict_is_remembered_and_a_later_confirmation_lifts_it():
    learned.record_junk("MT-161")
    assert learned.is_junk("MT-161") is True
    learned.record_sku("MT-161", strong=True)
    assert learned.is_junk("MT-161") is False


def test_what_was_learned_is_still_there_after_the_process_reads_the_file_again(store):
    learned.record_fix("PHC-ELE-8K", "PHC-ELE-BK")
    learned.record_junk("MT-161")
    learned.reload_from_disk()
    assert learned.fix("PHC-ELE-8K") == "PHC-ELE-BK"
    assert learned.known("PHC-ELE-BK") == "PHC-ELE-BK"
    assert learned.is_junk("MT-161") is True
    assert json.loads(store.read_text(encoding="utf-8"))["fixes"] == {"PHC-ELE-8K": "PHC-ELE-BK"}


def test_forgetting_a_correction_removes_it():
    learned.record_fix("PHC-ELE-8K", "PHC-ELE-BK")
    assert learned.forget("fixes", "PHC-ELE-8K") is True
    assert learned.fix("PHC-ELE-8K") is None
    assert learned.forget("fixes", "PHC-ELE-8K") is False
