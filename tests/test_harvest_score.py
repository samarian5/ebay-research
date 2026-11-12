"""Gate tests for the opportunity scorer — no network, no database.

Each case is a claim from Carson's videos turned into an assertion, so a future
refactor cannot quietly invert a rule.
"""
import unittest

from harvest.score import score_item


def item(**kw):
    base = {"item_id": "1", "title": "Vintage brass pocket watch", "price_usd": 60,
            "watchers": None, "units_sold": None, "quantity_n": 5}
    base.update(kw)
    return base


def s(**kw):
    return score_item(item(**kw), (None, None, 1), 3)


class Gates(unittest.TestCase):
    def test_50_watchers_is_copy(self):
        self.assertEqual(s(watchers=50)["verdict"], "copy")

    def test_49_watchers_is_watch(self):
        self.assertEqual(s(watchers=49)["verdict"], "watch")

    def test_50_sold_proves_demand_without_watchers(self):
        self.assertEqual(s(units_sold=50)["verdict"], "copy")

    def test_under_20_dollars_is_pass_even_with_watchers(self):
        r = s(watchers=889, price_usd=1.49)
        self.assertEqual(r["verdict"], "pass")
        self.assertIn("under his $20 floor", " ".join(r["reasons"]))

    def test_avoid_list_beats_demand(self):
        r = s(title="Funny graphic t-shirt", watchers=900, units_sold=900)
        self.assertEqual(r["verdict"], "pass")
        self.assertIn("avoid list", " ".join(r["reasons"]))

    def test_no_signal_is_unmeasured_not_zero(self):
        self.assertEqual(s()["verdict"], "unmeasured")


class Economics(unittest.TestCase):
    def test_fee_matches_1325_plus_30c(self):
        r = s(price_usd=100, watchers=60)
        self.assertAlmostEqual(r["profit_per_unit"], round(100 - 13.55 - 35, 2), places=2)

    def test_one_off_antique_gets_no_fabricated_margin(self):
        r = s(price_usd=1393, watchers=66)
        self.assertIsNone(r["profit_per_unit"])
        self.assertEqual(r["action"], "find source first")

    def test_monthly_profit_needs_velocity(self):
        r = s(price_usd=80, watchers=60)
        self.assertIsNone(r["est_profit_month"])

    def test_monthly_profit_from_two_snapshots(self):
        r = score_item(item(price_usd=80, watchers=60), (1.0, 2.0, 2), 3)
        self.assertAlmostEqual(r["est_profit_month"], round(r["profit_per_unit"] * 2 * 30, 2), places=2)


class Ranking(unittest.TestCase):
    def test_copy_outranks_pass_at_same_price(self):
        a = s(price_usd=70, watchers=80)["score"]
        b = s(price_usd=70, watchers=5)["score"]
        self.assertGreater(a, b)

    def test_cheap_high_watch_item_loses_to_real_candidate(self):
        trap = s(price_usd=9.95, watchers=1723)["score"]
        real = s(price_usd=75, watchers=60, units_sold=60)["score"]
        self.assertLess(trap, real)


if __name__ == "__main__":
    unittest.main()


def test_families_do_not_chain_unrelated_products():
    """Regression: shared-token counting merged an enamel pin with a Pokemon card
    into one 894-item family, which silently destroyed the competition metric."""
    from harvest.score import families

    items = [
        {"item_id": "a", "title": "Sabastian Michaelis Black Butler Collectible Enamel Pin"},
        {"item_id": "b", "title": "Bubble Arcanine Special Doujin Art Starry Holo Pokemon Card"},
        {"item_id": "c", "title": "Naruto Shippuden Kakashi Collectible Enamel Pin Anime Brooch"},
    ]
    fam = families(items)
    # three different products: different franchise, different category
    assert fam["b"] != fam["a"], "a trading card must not join a pin family"
    assert fam["c"] != fam["a"], "pins of different franchises are not one product"


def test_families_group_variants_of_one_product():
    from harvest.score import families

    items = [
        {"item_id": "1", "title": 'Sports Jersey Display Case Frame Shadow Box 24"x36" UV Protection'},
        {"item_id": "2", "title": 'Sports Jersey Display Case Frame Shadow Box 24"x36" UV Protection Black'},
        {"item_id": "3", "title": "Junk Silver Coins $1 Face Value Mixed Average Circulated"},
    ]
    fam = families(items)
    assert fam["1"] == fam["2"]
    assert fam["3"] not in (fam["1"],)
