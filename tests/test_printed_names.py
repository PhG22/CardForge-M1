"""Portuguese printed names in cards.db (Scryfall all_cards: lang + printed_name).

A card printed in Portuguese has the same set and number as the English one ("U 0263 LCI · PT"),
so its title confirms the print found by set + number, and identifies it alone when the number
was not read. The all_cards file (~400 MB) is streamed: only the lines of the wanted languages
are parsed, nothing of it goes to disk.
"""

import gzip
import io
import json
import sqlite3

import pytest

from cardforge.cv.config import VisionConfig
from cardforge.cv.matching import CardMatcher, InfoLineParser, MatchMethod, NameIndex, SetCodeCatalog
from cardforge.cv.ocr.fields import ExtractedFields
from cardforge.cv.ocr.rois import CardLayout
from cardforge.data.repository import SqliteCardRepository
from cardforge.data.schema import SCHEMA_VERSION
from cardforge.data.sync import (ScryfallSyncService, build_database, iter_jsonl_chunks, lang_filter,
                                 localized_prints)

SETS = [{"code": "lci", "name": "The Lost Caverns of Ixalan", "set_type": "expansion", "released_at": "2023-11-17"},
        {"code": "iko", "name": "Ikoria", "set_type": "expansion", "released_at": "2020-04-24"},
        {"code": "m21", "name": "Core Set 2021", "set_type": "core", "released_at": "2020-07-03"}]


def en(id_, name, set_, num, oracle, type_line="Creature", layout="normal", released="2023-11-17"):
    return {"id": id_, "oracle_id": oracle, "lang": "en", "name": name, "set": set_, "collector_number": num,
            "color_identity": [], "type_line": type_line, "mana_cost": "{1}", "rarity": "common",
            "layout": layout, "released_at": released}


def pt(id_, set_, num, oracle, printed=None, faces=None, lang="pt", **extra):
    card = {"id": id_, "oracle_id": oracle, "lang": lang, "name": "(english name)", "set": set_,
            "collector_number": num, "layout": "transform" if faces else "normal", **extra}
    if printed:
        card["printed_name"] = printed
    if faces:
        card["card_faces"] = [{"name": f"face {i}", "printed_name": f} for i, f in enumerate(faces)]
    return card


DEFAULT_CARDS = [
    en("e1", "Swashbuckler's Whip", "lci", "263", "o1", "Artifact — Equipment"),
    en("e2", "Restless Ridgeline", "lci", "283", "o2", "Land"),
    en("e3", "Sea-Dasher Octopus", "iko", "66", "o3", "Creature — Octopus", released="2020-04-24"),
    en("e4", "Delver of Secrets // Insectile Aberration", "lci", "300", "o4", "Creature // Creature", "transform"),
    en("e5", "Raptor Hatchling", "lci", "155", "o5"),
    en("e6", "Watcher A", "m21", "10", "o6"),
    en("e7", "Watcher B", "m21", "11", "o7"),
]
ALL_CARDS = DEFAULT_CARDS + [
    pt("p1", "lci", "263", "o1", "Chicote do Espadachim"),
    pt("p2", "lci", "283", "o2", "Crista de Serra Inquieta"),
    pt("p3", "iko", "66", "o3", "Polvo Flecha-do-mar"),
    pt("p4", "lci", "300", "o4", faces=["Vasculhador de Segredos", "Aberração Insetoide"]),
    pt("p5", "lci", "999", "o1", "Raptor Hatchling"),            # = an English name of another card: English wins
    pt("p6", "m21", "10", "o6", "Vigia"),                         # two cards with the same translation:
    pt("p7", "m21", "11", "o7", "Vigia"),
    pt("p8", "iko", "11", "o7", "Vigia"),                         # ... the one with more prints keeps it
    pt("p9", "lci", "263", "o1", "Fouet du bretteur", lang="fr"),
    pt("p10", "lci", "264", "o1", "Chicote Digital", digital=True),
]


def jsonl_gz(cards, members=1) -> bytes:
    lines = [json.dumps(c, separators=(",", ":"), ensure_ascii=False) for c in cards]
    size = -(-len(lines) // members)
    return b"".join(gzip.compress(("\n".join(lines[i:i + size]) + "\n").encode()) for i in range(0, len(lines), size))


def chunks(data: bytes, n: int = 7):
    return [data[i:i + n] for i in range(0, len(data), n)]


# ------------------------------------------------------------------ streaming all_cards

def test_streamed_lines_are_filtered_before_parsing():
    parsed = list(iter_jsonl_chunks(chunks(jsonl_gz(ALL_CARDS)), keep=lang_filter(["pt"])))
    assert {c["id"] for c in parsed} == {"p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p10"}


def test_stream_with_several_gzip_members_or_no_gzip():
    data = jsonl_gz(ALL_CARDS, members=3)
    assert [c["id"] for c in iter_jsonl_chunks(chunks(data, 5))] == [c["id"] for c in ALL_CARDS]
    plain = "\n".join(json.dumps(c) for c in ALL_CARDS).encode()   # transport already decompressed
    assert len(list(iter_jsonl_chunks(chunks(plain, 11)))) == len(ALL_CARDS)


def test_truncated_download_is_an_error():
    data = jsonl_gz(ALL_CARDS)
    with pytest.raises(EOFError):
        list(iter_jsonl_chunks(chunks(data[:-30])))


def test_localized_prints_keep_physical_cards_with_a_printed_name():
    found = list(localized_prints(ALL_CARDS, ["pt"]))
    assert {p.print_id for p in found} == {"p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"}   # no fr, no digital
    delver = next(p for p in found if p.print_id == "p4")
    assert delver.printed_name == "Vasculhador de Segredos // Aberração Insetoide"


# ------------------------------------------------------------------ cards.db

@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    db = tmp_path_factory.mktemp("ptdb") / "cards.db"
    build_database(DEFAULT_CARDS, SETS, db, bulk_updated_at="test",
                   localized=localized_prints(ALL_CARDS, ["pt"]), printed_langs=["pt"])
    r = SqliteCardRepository(db)
    yield r
    r.close()


def test_printed_names_are_stored_per_print(repo):
    assert repo.has_printed_names
    whip = repo.find_print("LCI", "0263")
    assert whip.name == "Swashbuckler's Whip" and whip.printed_names == ("Chicote do Espadachim",)
    assert repo.find_print("lci", "300").printed_names == ("Vasculhador de Segredos // Aberração Insetoide",)
    assert repo.find_print("lci", "155").printed_names == ()
    meta = repo.metadata()
    assert meta["schema_version"] == SCHEMA_VERSION and meta["printed_langs"] == "pt"
    assert meta["localized_prints"] == "8"


def test_portuguese_names_join_the_name_index(repo):
    langs = dict(repo.conn.execute("SELECT name_key, lang FROM card_name"))
    assert langs["chicote do espadachim"] == "pt" and langs["swashbuckler's whip"] == "en"
    assert langs["vasculhador de segredos"] == "pt" and langs["aberração insetoide"] == "pt"
    # an English name is never taken over by a translation of another card
    assert langs["raptor hatchling"] == "en"
    assert repo.find_latest_print_by_name("raptor hatchling").name == "Raptor Hatchling"
    # same translation for two cards: the one printed more often with it keeps the name
    assert repo.find_latest_print_by_name("vigia").name == "Watcher B"
    assert repo.find_latest_print_by_name("chicote do espadachim").name == "Swashbuckler's Whip"


def test_old_database_without_printed_names_still_works(tmp_path):
    db = tmp_path / "cards.db"
    build_database(DEFAULT_CARDS, SETS, db, bulk_updated_at="test")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE card_print_name")                         # a cards.db from schema 1
    conn.commit()
    conn.close()
    old = SqliteCardRepository(db)
    assert not old.has_printed_names and old.find_print("lci", "263").printed_names == ()
    old.close()


# ------------------------------------------------------------------ identification

@pytest.fixture(scope="module")
def matcher(repo):
    names = NameIndex(repo.all_name_keys())
    return CardMatcher(repo, names, VisionConfig()), InfoLineParser(SetCodeCatalog(repo.all_set_codes())), names


def match(matcher, name, typ, info):
    m, parser, _ = matcher
    return m.match(ExtractedFields(name, typ, info), parser.parse(info), CardLayout.STANDARD)


@pytest.mark.parametrize("title", ["Chicote do Espadachim", "ChicotedoEspadachim", "Chicote do Espadachirn"])
def test_portuguese_title_confirms_set_and_number(matcher, title):
    r = match(matcher, title, "Artefato — Equipamento", "U 0263 LCI PT JOHN DOE")
    assert r.method is MatchMethod.SET_NUMBER and r.confidence == 100 and not r.needs_review
    assert r.card.name == "Swashbuckler's Whip" and r.printed_name_match


def test_portuguese_title_alone_finds_the_card(matcher):
    r = match(matcher, "Polvo Flecha-do-mar", "Criatura — Polvo", "")
    assert r.method is MatchMethod.NAME_EXACT and r.card.name == "Sea-Dasher Octopus"


def test_back_face_in_portuguese(matcher):
    r = match(matcher, "Aberração Insetoide", "", "U 0300 LCI PT")
    assert r.method is MatchMethod.SET_NUMBER and r.card.name.startswith("Delver of Secrets")


def test_portuguese_title_of_another_card_still_goes_to_review(matcher):
    # set + number point to Restless Ridgeline, the title is the Whip's: misread number
    r = match(matcher, "Chicote do Espadachim", "Artefato — Equipamento", "U 0283 LCI PT")
    assert r.method is MatchMethod.NAME_SET_MISMATCH and r.needs_review
    assert r.card.name == "Swashbuckler's Whip"


def test_portuguese_titles_score_high_for_the_orientation(matcher):
    names = matcher[2]
    assert names.best_score("Chicote do Espadachim") == 100
    assert names.best_score("ChicotedoEspadachim") == 100


# ------------------------------------------------------------------ sync service

class FakeResponse:
    def __init__(self, body=None, data=b""):
        self.body, self.data, self.headers = body, data, {"Content-Length": str(len(data))}

    def json(self):
        return self.body

    def iter_content(self, chunk_size=1):
        return iter(chunks(self.data, 1000))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_scryfall(monkeypatch, fetched: list, updated_at="2026-11-17T10:00:00+00:00"):
    files = {"https://x/default.jsonl.gz": jsonl_gz(DEFAULT_CARDS), "https://x/all.jsonl.gz": jsonl_gz(ALL_CARDS)}
    index = {"data": [
        {"type": "default_cards", "updated_at": updated_at, "jsonl_download_uri": "https://x/default.jsonl.gz",
         "compressed_size": len(files["https://x/default.jsonl.gz"])},
        {"type": "all_cards", "updated_at": updated_at, "jsonl_download_uri": "https://x/all.jsonl.gz",
         "compressed_size": len(files["https://x/all.jsonl.gz"])}]}

    def get(self, url, **kw):
        fetched.append(url)
        if url.endswith("/bulk-data"):
            return FakeResponse(index)
        if url.endswith("/sets"):
            return FakeResponse({"data": SETS, "has_more": False})
        return FakeResponse(data=files[url])
    monkeypatch.setattr(ScryfallSyncService, "_get", get)


def test_sync_downloads_the_portuguese_names(tmp_path, monkeypatch):
    fetched = []
    fake_scryfall(monkeypatch, fetched)
    db = tmp_path / "cards.db"
    report = ScryfallSyncService(db, printed_langs=["pt"]).sync()
    assert report.updated and report.printed_langs == ("pt",)
    assert report.localized_prints == 8 and report.localized_names > 0
    assert "pt names" in str(report)
    assert "https://x/all.jsonl.gz" in fetched
    repo = SqliteCardRepository(db)
    assert repo.find_print("iko", "66").printed_names == ("Polvo Flecha-do-mar",)
    repo.close()

    fetched.clear()
    assert not ScryfallSyncService(db, printed_langs=["pt"]).sync().updated      # same bulk, same languages
    assert "https://x/all.jsonl.gz" not in fetched

    fetched.clear()
    report = ScryfallSyncService(db, printed_langs=[]).sync()                     # languages changed: rebuild
    assert report.updated and report.localized_prints == 0
    assert "https://x/all.jsonl.gz" not in fetched                               # English only: no all_cards
