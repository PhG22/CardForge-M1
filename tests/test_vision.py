"""
Testes unitários do subsistema de visão (sem câmara, sem rede, sem modelos de OCR).

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cardforge.cv.config import VisionConfig  # noqa: E402
from cardforge.cv.detection import CardDetector, PerspectiveRectifier, order_points  # noqa: E402
from cardforge.cv.matching import (CardMatcher, InfoLineParser, MatchMethod, NameIndex,  # noqa: E402
                                   SetCodeCatalog)
from cardforge.cv.matching.type_words import card_types, types_agree  # noqa: E402
from cardforge.cv.ocr.fields import ExtractedFields  # noqa: E402
from cardforge.cv.ocr.layout import LayoutClassifier  # noqa: E402
from cardforge.cv.ocr.rois import CardLayout  # noqa: E402
from cardforge.data.repository import SqliteCardRepository, normalize_number  # noqa: E402
from cardforge.data.sync import build_database  # noqa: E402

SETS = [
    {"code": "fic", "name": "Final Fantasy Commander", "set_type": "commander", "released_at": "2025-06-13"},
    {"code": "tfic", "name": "FIC Tokens", "set_type": "token", "parent_set_code": "fic", "released_at": "2025-06-13"},
    {"code": "fin", "name": "Final Fantasy", "set_type": "expansion", "released_at": "2025-06-13"},
    {"code": "iko", "name": "Ikoria", "set_type": "expansion", "released_at": "2020-04-24"},
    {"code": "tle", "name": "Avatar Eternal", "set_type": "expansion", "released_at": "2025-11-21"},
    {"code": "plst", "name": "The List", "set_type": "masters", "released_at": "2026-10-19"},
]


def _card(id_, name, set_, num, oracle, colors="", type_line="Creature", layout="normal",
          released="2025-06-13", **extra):
    return {"id": id_, "oracle_id": oracle, "name": name, "set": set_, "collector_number": num,
            "color_identity": list(colors), "type_line": type_line, "mana_cost": "{1}", "rarity": "rare",
            "layout": layout, "released_at": released, **extra}


CARDS = [
    _card("p1", "Summon: Kujata", "fic", "61", "o1", "R", "Enchantment Creature — Saga Ox", "saga"),
    _card("p2", "Rebel", "tfic", "7", "o2", "R", "Token Creature — Rebel", "token"),
    _card("p3", "Prickly Marmoset", "iko", "129", "o3", "R", "Creature — Monkey", released="2020-04-24"),
    _card("p4", "Forest", "fin", "306", "o4", "G", "Basic Land — Forest"),
    _card("p5", "Tifa, Martial Artist", "fic", "6", "o5", "RGW", "Legendary Creature — Human Monk"),
    _card("p6", "Lita, Mechanical Engineer", "tle", "4", "o6", "W", "Legendary Artifact Creature — Artificer",
          flavor_name="The Mechanist, Tactical Tinkerer"),
    _card("p7", "Secret Rendezvous", "fic", "219", "o7", "W", "Sorcery"),
    _card("p8", "Secret Rendezvous", "plst", "STX-26", "o7", "W", "Sorcery", released="2026-10-19"),
    _card("p9", "Digital Only", "fic", "999", "o9", digital=True),
    _card("p10", "Fire // Ice", "fin", "200", "o10", "UR", "Instant // Instant", "split"),
]


class DatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "cards.db"
        cls.prints, cls.n_names = build_database(CARDS, SETS, cls.db, bulk_updated_at="test")
        cls.repo = SqliteCardRepository(cls.db)
        cls.names = NameIndex(cls.repo.all_name_keys())
        cls.sets = SetCodeCatalog(cls.repo.all_set_codes())
        cls.cfg = VisionConfig()
        cls.matcher = CardMatcher(cls.repo, cls.names, cls.cfg)
        cls.parser = InfoLineParser(cls.sets)

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()
        cls.tmp.cleanup()

    def match(self, name, typ, info, layout=CardLayout.STANDARD):
        fields = ExtractedFields(name, typ, info)
        return self.matcher.match(fields, self.parser.parse(info), layout)


class TestCardDatabase(DatabaseTestCase):
    def test_build_skips_digital_and_indexes_faces_and_flavor_names(self):
        self.assertEqual(self.prints, 9)
        keys = set(self.repo.all_name_keys())
        self.assertNotIn("digital only", keys)
        self.assertTrue({"fire // ice", "fire", "ice", "the mechanist, tactical tinkerer"} <= keys)

    def test_find_print_normalizes_collector_number(self):
        card = self.repo.find_print("FIC", "0061")
        self.assertEqual(card.name, "Summon: Kujata")
        self.assertEqual(card.color_identity_str, "R")

    def test_latest_print_by_name_prefers_regular_sets(self):
        self.assertEqual(self.repo.find_latest_print_by_name("secret rendezvous").set_code, "FIC")

    def test_colorless_and_multicolor(self):
        self.assertTrue(self.repo.find_print("fic", "6").is_multicolor)
        self.assertEqual(self.repo.find_print("fin", "306").color_identity_str, "G")


class TestInfoLineParser(DatabaseTestCase):
    def test_modern_info_line(self):
        info = self.parser.parse("R0061 FFVII FICEN JWALEXANDREHONORE")
        self.assertEqual((info.set_code, info.collector_number), ("FIC", "0061"))

    def test_language_glued_to_code(self):
        self.assertEqual(self.parser.parse("M0004 TLEENBI:WATER,CH:17").set_code, "TLE")

    def test_ocr_confusions(self):
        self.assertEqual(self.parser.parse("156/274U JKOPT KYIONG").set_code, "IKO")
        self.assertEqual(self.parser.parse("0306 F1N EN").set_code, "FIN")

    def test_number_over_total_beats_copyright_year(self):
        self.assertEqual(self.parser.collector_number("JonasDeRo ©2013 Wizards 236/249"), "236")

    def test_token_mark(self):
        self.assertTrue(self.parser.has_token_mark("T0002 FFVII FIC·EN"))
        self.assertFalse(self.parser.has_token_mark("R0061 FFVII FICEN TONY"))

    def test_unknowns(self):
        info = self.parser.parse("")
        self.assertFalse(info.has_set or info.has_number)


class TestCardMatcher(DatabaseTestCase):
    def test_set_number_confirmed_by_name(self):
        m = self.match("Summon:Kujata", "EnchantmentCreature-Saga Ox", "R0061 FICEN", CardLayout.SAGA)
        self.assertEqual((m.method, m.confidence, m.needs_review), (MatchMethod.SET_NUMBER, 100, False))

    def test_token_set_prefix(self):
        m = self.match("REBEL", "Token Creature-Rebel", "T0007 FIC EN", CardLayout.TOKEN_FULL)
        self.assertEqual((m.card.name, m.card.set_code), ("Rebel", "TFIC"))

    def test_localized_card_confirmed_by_type(self):
        m = self.match("Sagui Espinhoso", "Criatura-Macaco", "129/274C IKO·PT")
        self.assertEqual((m.method, m.card.name), (MatchMethod.SET_NUMBER_LOCALIZED, "Prickly Marmoset"))
        self.assertGreaterEqual(m.confidence, self.cfg.confidence_threshold)

    def test_localized_card_with_wrong_type_needs_review(self):
        m = self.match("Sagui Espinhoso", "Terreno", "129/274C IKO·PT")
        self.assertEqual(m.method, MatchMethod.SET_NUMBER_NAME_MISMATCH)
        self.assertTrue(m.needs_review)
        self.assertLess(m.confidence, self.cfg.confidence_threshold)

    def test_name_disagrees_with_set_number(self):
        m = self.match("Forest", "Basic Land", "0006 FIC EN")  # nome de outra carta
        self.assertEqual((m.method, m.card.name, m.needs_review), (MatchMethod.NAME_SET_MISMATCH, "Forest", True))

    def test_flavor_name_is_the_same_card(self):
        m = self.match("TheMechanist,TacticalTinkel", "LegendaryArtifact Creature", "M0004 TLEENBI")
        self.assertEqual((m.method, m.card.name, m.needs_review), (MatchMethod.SET_NUMBER, "Lita, Mechanical Engineer", False))

    def test_name_fallbacks(self):
        self.assertEqual(self.match("Tifa,MartialArtist", "", "").method, MatchMethod.NAME_EXACT)
        fuzzy = self.match("Tifa, Martal Artist", "", "")
        self.assertEqual((fuzzy.method, fuzzy.card.name), (MatchMethod.NAME_FUZZY, "Tifa, Martial Artist"))
        self.assertEqual(self.match("zzzz qqqq", "", "").method, MatchMethod.NONE)

    def test_name_lookup_prefers_ocr_set(self):
        self.assertEqual(self.match("Secret Rendezvous", "Sorcery", "FIC EN").card.set_code, "FIC")


class TestHelpers(unittest.TestCase):
    def test_normalize_number(self):
        self.assertEqual([normalize_number(n) for n in ("0092", "007a", "0", "STX-26")], ["92", "7a", "0", "stx-26"])

    def test_type_words(self):
        self.assertEqual(card_types("Criatura Lendária — Humano"), {"creature"})
        self.assertEqual(card_types("EnchantmentCreature-Saga Ox"), {"enchantment", "creature"})
        self.assertTrue(types_agree("Mágica Instantânea", "Instant"))
        self.assertFalse(types_agree("", "Instant"))

    def test_layout_decision(self):
        d = LayoutClassifier.decide
        self.assertEqual(d("", "saga", ""), CardLayout.SAGA)
        self.assertEqual(d("", "", "basic land - forest"), CardLayout.FULL_ART)
        self.assertEqual(d("", "", "token creature"), CardLayout.TOKEN_FULL)
        self.assertEqual(d("ficha de criatura", "", ""), CardLayout.TOKEN_CLASSIC)
        self.assertEqual(d("", "", ""), CardLayout.STANDARD)

    def test_config_toml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vision.toml"
            p.write_text('db_path = "x.db"\nconfidence_threshold = 95\n[camera]\nresolution = [640, 480]\n',
                         encoding="utf-8")
            cfg = VisionConfig.load(p)
            self.assertEqual((cfg.confidence_threshold, cfg.camera.resolution), (95, (640, 480)))
            self.assertTrue(cfg.db_path.endswith("x.db"))
            p.write_text("typo_option = 1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                VisionConfig.load(p)


class TestDetection(unittest.TestCase):
    def synthetic_photo(self, angle=12.0):
        """Carta (borda preta, interior colorido) inclinada sobre fundo claro."""
        img = np.full((1600, 1200, 3), 215, np.uint8)
        card = np.zeros((880, 630, 3), np.uint8)
        card[25:-25, 25:-25] = (40, 120, 200)
        center = np.array([600, 800])
        rect = ((float(center[0]), float(center[1])), (630, 880), angle)
        box = cv2.boxPoints(rect).astype(np.float32)
        h = cv2.getPerspectiveTransform(np.float32([[0, 0], [629, 0], [629, 879], [0, 879]]), order_points(box))
        warped = cv2.warpPerspective(card, h, (1200, 1600))
        mask = cv2.warpPerspective(np.full((880, 630), 255, np.uint8), h, (1200, 1600))
        img[mask > 0] = warped[mask > 0]
        return img, box

    def test_detects_and_rectifies_card(self):
        cfg = VisionConfig()
        img, box = self.synthetic_photo()
        quad = CardDetector(cfg).detect(img)
        self.assertIsNotNone(quad)
        err = np.abs(order_points(quad.corners) - order_points(box)).max()
        self.assertLess(err, 25)  # px na resolução original
        card = PerspectiveRectifier(cfg).rectify(img, quad)
        self.assertEqual(card.image.shape, (880, 630, 3))

    def test_empty_frame_is_crop_failure(self):
        self.assertIsNone(CardDetector(VisionConfig()).detect(np.full((1600, 1200, 3), 200, np.uint8)))


if __name__ == "__main__":
    unittest.main()
