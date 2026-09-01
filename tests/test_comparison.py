"""Сравнение токенной защиты с регулярным baseline на одних и тех же запросах.

Таблица из README — вывод этого теста, а не заявление. Прогнать с выводом:
    pytest tests/test_comparison.py -s
"""
import baseline_regex
from test_guardrails import ATTACKS, LEGIT

from text2sql import guardrails


def _score(check) -> tuple[int, int]:
    """Возвращает (заблокировано атак, пропущено легитимных)."""
    blocked = sum(1 for _, sql in ATTACKS if not check(sql).ok)
    passed = sum(1 for _, sql in LEGIT if check(sql).ok)
    return blocked, passed


def test_token_blocks_all_attacks_and_passes_all_legit():
    blocked, passed = _score(guardrails.validate_and_sanitize_select)
    assert (blocked, passed) == (len(ATTACKS), len(LEGIT))  # 26/26 и 18/18


def test_regex_baseline_misses_functions_and_false_positives():
    blocked, passed = _score(baseline_regex.validate_and_sanitize_select)
    assert (blocked, passed) == (20, 14)  # пропускает 6 атак, ложно рубит 4 легит


def test_print_comparison(capsys):
    with capsys.disabled():
        print(f"\n{'реализация':<24}{'заблокировано атак':>20}{'пропущено легит':>18}")
        for name, check in [
            ("поиск слов регуляркой", baseline_regex.validate_and_sanitize_select),
            ("разбор по токенам", guardrails.validate_and_sanitize_select),
        ]:
            blocked, passed = _score(check)
            print(f"{name:<24}{blocked:>15}/{len(ATTACKS):<4}{passed:>13}/{len(LEGIT):<4}")
