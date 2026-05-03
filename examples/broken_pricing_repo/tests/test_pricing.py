import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pricing.calc import apply_tax, compute_discount, final_price


def test_no_discount():
    assert compute_discount(100, None) == 100


def test_with_discount():
    class Discount:
        percent = 0.1

    assert compute_discount(100, Discount()) == 10.0


def test_apply_tax():
    assert apply_tax(100, 0.08) == 108.0


def test_final_price_no_discount():
    assert final_price(100, discount=None, tax_rate=0.0) == 100.0
