import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
import update_afa as u  # noqa: E402

OCT = (2026, 10)


def pick(text, now=OCT):
    return u.select(u.find_all(u.html_to_text(text)), now)


class ParserTests(unittest.TestCase):
    def test_current_month(self):
        p, amb, _ = pick("The Automatic Fuel Adjustment (AFA) for October 2026 is equivalent to +3.61 sen/kWh.")
        self.assertEqual((round(p[0], 2), p[1]), (3.61, OCT))

    def test_html_and_entities(self):
        p, _, _ = pick("<p>AFA for <strong>October 2026</strong> is <b>3.61</b>&nbsp;sen/kWh</p>")
        self.assertAlmostEqual(p[0], 3.61)

    def test_history_table_prefers_current_month(self):
        p, _, _ = pick("AFA history: August 2026 3.80 sen/kWh. September 2026 3.67 sen/kWh. October 2026 3.61 sen/kWh.")
        self.assertAlmostEqual(p[0], 3.61)

    def test_two_months_in_one_sentence(self):
        p, _, _ = pick("AFA rose from 3.67 sen/kWh in September 2026 to 3.61 sen/kWh for October 2026.")
        self.assertAlmostEqual(p[0], 3.61)

    def test_malay_rm_per_kwh(self):
        p, _, _ = pick("Kadar AFA untuk bulan Oktober 2026: RM0.0361/kWh")
        self.assertAlmostEqual(p[0], 3.61)

    def test_caps_are_not_the_rate(self):
        p, _, _ = pick("AFA is capped at +/-3 sen/kWh, effective July 2025")
        self.assertIsNone(p)

    def test_old_month_is_reported_as_old(self):
        p, _, _ = pick("AFA for September 2026 was 3.67 sen/kWh")
        self.assertEqual(p[1], (2026, 9))

    def test_cap_in_the_next_sentence_does_not_hide_the_rate(self):
        p, _, _ = pick("<p>The AFA for <b>October 2026</b> is +3.45 sen/kWh for domestic users.</p><p>The mechanism is capped at &plusmn;3 sen/kWh trigger band.</p>")
        self.assertAlmostEqual(p[0], 3.45)

    def test_cap_in_the_same_sentence_still_blocks(self):
        p, _, _ = pick("AFA is capped at 3 sen/kWh from October 2026 onwards.")
        self.assertIsNone(p)

    def test_energy_charge_is_not_afa(self):
        p, _, _ = pick("Energy charge is RM0.2703/kWh. AFA for October 2026 is 3.61 sen/kWh")
        self.assertAlmostEqual(p[0], 3.61)

    def test_year_rollover(self):
        p, _, _ = pick("AFA for January 2027 is 3.50 sen/kWh", now=(2026, 12))
        self.assertEqual(p[1], (2027, 1))


def R(rate, ym, src="sb"):
    return (src, (rate, ym), False)


class DecisionTests(unittest.TestCase):
    def d(self, today, old_rate, old_month, readings, errors=(), n=2):
        return u.decide(today, old_rate, old_month, readings, list(errors), n)

    def test_new_month_updates(self):
        a, _, rate, ym = self.d(date(2026, 11, 1), 3.61, (2026, 10), [R(3.40, (2026, 11))])
        self.assertEqual((a, rate, ym), ("update", 3.40, (2026, 11)))

    def test_same_value_is_no_change(self):
        a, *_ = self.d(date(2026, 10, 14), 3.61, (2026, 10), [R(3.61, OCT)])
        self.assertEqual(a, "nochange")

    def test_same_rate_new_month_still_updates_the_month(self):
        a, _, rate, ym = self.d(date(2026, 11, 1), 3.61, (2026, 10), [R(3.61, (2026, 11))])
        self.assertEqual((a, ym), ("update", (2026, 11)))

    def test_next_month_announced_early_is_not_applied(self):
        a, *_ = self.d(date(2026, 10, 28), 3.61, OCT, [R(3.40, (2026, 11))])
        self.assertEqual(a, "notice")

    def test_conflict_fails(self):
        a, *_ = self.d(date(2026, 11, 5), 3.61, OCT, [R(3.40, (2026, 11), "a"), R(3.55, (2026, 11), "b")])
        self.assertEqual(a, "error")

    def test_huge_jump_fails(self):
        a, *_ = self.d(date(2026, 11, 5), 3.61, OCT, [R(9.90, (2026, 11))])
        self.assertEqual(a, "error")

    def test_all_sources_down_fails(self):
        a, *_ = self.d(date(2026, 11, 5), 3.61, OCT, [], errors=["x", "y"])
        self.assertEqual(a, "error")

    def test_one_source_down_is_ok_if_other_answers(self):
        a, *_ = self.d(date(2026, 11, 5), 3.61, OCT, [R(3.40, (2026, 11))], errors=["x"])
        self.assertEqual(a, "update")

    def test_nothing_published_yet_is_tolerated_on_day_1_and_2_only(self):
        empty = [("sb", None, False)]
        self.assertEqual(self.d(date(2026, 11, 1), 3.61, OCT, empty)[0], "notice")
        self.assertEqual(self.d(date(2026, 11, 3), 3.61, OCT, empty)[0], "error")

    def test_stored_month_ahead_is_left_alone(self):
        a, *_ = self.d(date(2026, 10, 14), 3.00, (2026, 11), [R(3.61, OCT)])
        self.assertEqual(a, "nochange")


if __name__ == "__main__":
    unittest.main()
