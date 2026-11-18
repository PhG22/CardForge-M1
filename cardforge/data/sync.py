"""
Espelho local do Scryfall (CV-SD-04): cria/atualiza o data/cards.db.

* default_cards (~80 MB gz): uma impressão por carta, em inglês (ou no único idioma em que saiu).
  Dá as impressões (edição + número), os nomes oficiais, as cores e os tipos.
* all_cards (~400 MB gz): todas as impressões em todos os idiomas. Só os nomes impressos dos idiomas
  pedidos (padrão: pt) são aproveitados — campos `lang` e `printed_name` — para que um título lido em
  português ("Chicote do Espadachim") confirme a carta tanto quanto o nome inglês ("Swashbuckler's Whip").
  O arquivo é lido em streaming direto da rede: nada dele é gravado no cartão SD, e só as linhas dos
  idiomas pedidos passam pelo json.loads (as outras são descartadas por uma busca de bytes).

"Já atualizado" (check()) = as MESMAS CARTAS, não o mesmo arquivo: a Scryfall regera os bulks todo dia
(preços, imagens, legalidades), o que não muda nada na identificação. O que conta é a lista de edições
físicas com o número de cartas de cada uma (sets_fingerprint: edição nova, cartas novas numa edição,
The List / Secret Lair), o schema do banco, os idiomas dos nomes e a idade do banco (max_age_days, para
pegar nomes em português que a Scryfall acrescenta depois do lançamento). Custa 2 requisições pequenas.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import itertools
import json
import logging
import os
import platform
import sqlite3
import tempfile
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, NamedTuple, Optional

from .records import COLOR_NAMES
from .repository import normalize_number
from .schema import SCHEMA_SQL, SCHEMA_VERSION

log = logging.getLogger(__name__)

SCRYFALL_API = "https://api.scryfall.com"
# A Scryfall exige User-Agent e Accept explícitos em todas as requisições.
HEADERS = {"User-Agent": "CardForge-M1/1.0", "Accept": "application/json"}

# Idiomas cujos nomes impressos entram no cards.db (vision.toml: printed_name_langs).
DEFAULT_PRINTED_LANGS = ("pt",)
# Mesmo sem cartas novas, o banco é refeito quando fica mais velho que isto (vision.toml: db_max_age_days).
DEFAULT_MAX_AGE_DAYS = 30.0
# (conexão, leitura) em segundos: sem rede ou com a Scryfall fora do ar, a checagem desiste em 10 s.
DEFAULT_TIMEOUT = (10.0, 60.0)

# Layouts que não representam cartas "jogáveis"; o nome delas só é indexado se não houver outro dono.
_TOKEN_LAYOUTS = {"token", "double_faced_token", "emblem"}
_LOW_PRIORITY_LAYOUTS = {"art_series"}
# Um nome traduzido nunca toma o lugar de um nome inglês igual (de outra carta).
_LOCALIZED_PRIORITY_OFFSET = 10
GZIP_MAGIC = bytes([0x1F, 0x8B])


class SyncCancelled(Exception):
    """The update was cancelled (should_stop): the local cards.db was not touched."""


@dataclass
class BulkEntry:
    updated_at: str
    download_uri: str
    size: int = 0                     # bytes compactados (compressed_size)


@dataclass
class SyncReport:
    updated: bool
    prints: int = 0
    names: int = 0
    sets: int = 0
    bulk_updated_at: str = ""
    seconds: float = 0.0
    printed_langs: tuple[str, ...] = ()
    localized_prints: int = 0         # impressões traduzidas cujo nome foi gravado (card_print_name)
    localized_names: int = 0          # nomes traduzidos novos no índice de nomes (card_name)

    def __str__(self) -> str:
        langs = ",".join(self.printed_langs) or "none"
        if not self.updated:
            return f"already up to date (bulk {self.bulk_updated_at}, printed names: {langs})"
        extra = (f", {self.localized_names} {langs} names from {self.localized_prints} {langs} prints"
                 if self.printed_langs else "")
        return (f"updated ({self.prints} prints, {self.names} names{extra}, {self.sets} sets, "
                f"bulk {self.bulk_updated_at}, {self.seconds:.0f}s)")


@dataclass
class SyncCheck:
    """Resultado de ScryfallSyncService.check(): o cards.db local precisa ser refeito?"""
    current: bool
    reason: str                       # "up to date" ou por que atualizar
    bulk: BulkEntry
    sets: list = field(default_factory=list, repr=False)
    fingerprint: str = ""
    localized_bulk: Optional[BulkEntry] = None    # all_cards (nomes impressos), se há idiomas pedidos

    @property
    def download_bytes(self) -> int:
        """Quanto uma atualização baixa (compactado): default_cards + all_cards."""
        return self.bulk.size + (self.localized_bulk.size if self.localized_bulk else 0)


class LocalizedPrint(NamedTuple):
    """Uma impressão noutro idioma (all_cards), só com o que o cards.db guarda dela."""
    lang: str
    set_code: str
    number: str
    print_id: str
    oracle_id: str
    printed_name: str                 # várias faces: "A // B"
    layout: str


def _name_priority(card: dict, is_face: bool) -> int:
    """Prioridade de quem fica com um name_key quando vários oracles têm o mesmo nome (ex.: tokens "Spirit")."""
    layout = card.get("layout", "")
    if layout in _LOW_PRIORITY_LAYOUTS:
        return 0
    base = 1 if layout in _TOKEN_LAYOUTS else 3
    return base - (1 if is_face else 0) + 1


def _merge_faces(card: dict, key: str) -> str:
    """Campos de cartas de várias faces ficam em card_faces; junta-os como "A // B"."""
    if card.get(key):
        return card[key]
    faces = card.get("card_faces") or []
    parts = [f.get(key, "") for f in faces if f.get(key)]
    return " // ".join(parts)


def sets_fingerprint(sets: Iterable[dict]) -> str:
    """Impressão digital do conteúdo que a identificação usa: as edições físicas e quantas cartas cada
    uma tem (card_count). Muda com edição nova ou cartas novas; não muda com preços/imagens."""
    items = sorted(f"{s.get('code', '').lower()}:{s.get('card_count', 0)}" for s in sets if not s.get("digital"))
    return hashlib.sha1("\n".join(items).encode()).hexdigest()


def _parse_time(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _oracle_id(card: dict) -> Optional[str]:
    return card.get("oracle_id") or next(
        (f.get("oracle_id") for f in card.get("card_faces") or [] if f.get("oracle_id")), None)


def iter_jsonl(stream: io.BufferedIOBase) -> Iterator[dict]:
    """Lê JSON Lines, compactado em gzip ou não (o servidor pode já ter descompactado o transporte)."""
    is_gzip = stream.read(2) == GZIP_MAGIC
    stream.seek(0)
    raw = gzip.open(stream, "rb") if is_gzip else stream
    with io.TextIOWrapper(raw, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _inflate(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Descompacta gzip em streaming (um ou vários membros); repassa os bytes se não for gzip."""
    it = iter(chunks)
    head = b""
    for chunk in it:
        head += chunk
        if len(head) >= 2:
            break
    if head[:2] != GZIP_MAGIC:
        yield head
        yield from it
        return
    decomp, fed = zlib.decompressobj(zlib.MAX_WBITS | 16), False
    for chunk in itertools.chain((head,), it):
        while chunk:
            fed = True
            out = decomp.decompress(chunk)
            if out:
                yield out
            chunk = b""
            if decomp.eof:                             # fim de um membro: o resto é o próximo
                chunk, decomp, fed = decomp.unused_data, zlib.decompressobj(zlib.MAX_WBITS | 16), False
    if fed and not decomp.eof:
        raise EOFError("download truncated: the gzip stream ended before its end marker")


def iter_jsonl_chunks(chunks: Iterable[bytes], keep: Optional[Callable[[bytes], bool]] = None) -> Iterator[dict]:
    """
    JSON Lines a partir de pedaços de bytes (download em streaming), compactado em gzip ou não.
    `keep(linha)` filtra as linhas ANTES do json.loads: a busca de bytes custa microssegundos e o parse
    de uma carta, dezenas — no all_cards só ~1 linha em 20 é de um idioma pedido.
    """
    tail = b""
    for data in _inflate(chunks):
        lines = (tail + data).split(b"\n")
        tail = lines.pop()
        for line in lines:
            if (keep is None or keep(line)) and line.strip():
                yield json.loads(line)
    if tail.strip() and (keep is None or keep(tail)):
        yield json.loads(tail)


def lang_filter(langs: Iterable[str]) -> Callable[[bytes], bool]:
    """Filtro barato de linhas do all_cards pelo idioma (JSON compacto do Scryfall: "lang":"pt")."""
    needles = tuple(f'"lang":"{lang}"'.encode() for lang in langs)
    return lambda line: any(n in line for n in needles)


def localized_prints(cards: Iterable[dict], langs: Iterable[str]) -> Iterator[LocalizedPrint]:
    """Impressões físicas nos idiomas pedidos que têm nome impresso (printed_name)."""
    wanted = set(langs)
    for card in cards:
        lang = card.get("lang")
        if lang not in wanted or card.get("digital"):
            continue
        faces = card.get("card_faces") or []
        printed = card.get("printed_name") or " // ".join(
            f["printed_name"].strip() for f in faces if f.get("printed_name"))
        oracle_id = _oracle_id(card)
        set_code = (card.get("set") or "").lower()
        number = str(card.get("collector_number") or "")
        if printed and oracle_id and set_code and number:
            yield LocalizedPrint(lang, set_code, number, card.get("id", ""), oracle_id, printed.strip(),
                                 card.get("layout", ""))


def build_database(cards: Iterable[dict], sets: list[dict], dest: Path,
                   bulk_updated_at: str = "", sets_updated_at: str = "",
                   localized: Iterable[LocalizedPrint] = (), printed_langs: Iterable[str] = (),
                   on_card: Optional[Callable[[int], None]] = None,
                   should_stop: Optional[Callable[[], bool]] = None) -> tuple[int, int]:
    """
    Constrói um cards.db novo em `dest` a partir das cartas do bulk "default_cards", da lista de sets
    e (opcional) das impressões traduzidas do bulk "all_cards". Devolve (prints, names) — names inclui
    os nomes traduzidos; quantos são traduzidos fica em db_metadata (localized_names).
    on_card(n): andamento, a cada 1000 cartas lidas; should_stop(): True = SyncCancelled.
    """
    started = time.time()
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(dest)
    try:
        conn.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;")
        conn.executescript(SCHEMA_SQL)
        conn.executemany("INSERT INTO color VALUES (?, ?)", COLOR_NAMES.items())

        conn.executemany(
            "INSERT OR IGNORE INTO mtg_set VALUES (?, ?, ?, ?, ?)",
            [(s["code"].lower(), s.get("name", ""), s.get("set_type"),
              (s.get("parent_set_code") or None), s.get("released_at")) for s in sets],
        )

        # name_key -> (prioridade, oracle_id, is_face, lang). Resolvido em memória e gravado no fim.
        names: dict[str, tuple[int, str, bool, str]] = {}
        oracles: set[str] = set()
        prints = lines = 0
        for card in cards:
            lines += 1
            if lines % 1000 == 0:
                if should_stop is not None and should_stop():
                    raise SyncCancelled()
                if on_card is not None:
                    on_card(lines)
            if card.get("digital"):  # MTGO / Arena não existem fisicamente
                continue
            oracle_id = _oracle_id(card)
            if not oracle_id:
                continue
            oracles.add(oracle_id)
            name = card.get("name", "")
            conn.execute(
                "INSERT OR IGNORE INTO oracle_card VALUES (?, ?, ?, ?)",
                (oracle_id, name, _merge_faces(card, "mana_cost"), _merge_faces(card, "type_line")),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO oracle_color VALUES (?, ?)",
                [(oracle_id, c) for c in card.get("color_identity", []) if c in COLOR_NAMES],
            )

            keys = [(name.lower(), False)]
            if " // " in name:
                keys += [(face.strip().lower(), True) for face in name.split(" // ")]
            # Nome impresso alternativo (Universes Beyond / Godzilla, ex.: "The Mechanist, Tactical Tinkerer").
            flavor_names = [card.get("flavor_name")] + [f.get("flavor_name") for f in card.get("card_faces") or []]
            keys += [(fn.lower(), True) for fn in flavor_names if fn]
            for key, is_face in keys:
                prio = _name_priority(card, is_face)
                if key and (key not in names or prio > names[key][0]):
                    names[key] = (prio, oracle_id, is_face, "en")

            set_code = card.get("set", "").lower()
            number = str(card.get("collector_number", ""))
            if set_code and number:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO card_print VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (card["id"], set_code, normalize_number(number), number, oracle_id,
                     card.get("rarity"), card.get("released_at"), card.get("layout")),
                )
                prints += cur.rowcount

        # Nomes traduzidos: o da própria impressão (cross-check de set + número) e, no índice de nomes,
        # os que não colidem com um nome inglês. Duas cartas com a mesma tradução: fica a de maior
        # prioridade e, empatadas, a que tem mais impressões com esse nome.
        localized_count = 0
        votes: dict[str, Counter] = {}
        for p in localized:
            if p.oracle_id not in oracles:
                continue
            cur = conn.execute("INSERT OR IGNORE INTO card_print_name VALUES (?, ?, ?, ?, ?)",
                               (p.set_code, normalize_number(p.number), p.lang, p.printed_name, p.print_id))
            localized_count += cur.rowcount
            keys = [(p.printed_name.lower(), False)]
            if " // " in p.printed_name:
                keys += [(face.strip().lower(), True) for face in p.printed_name.split(" // ")]
            for key, is_face in keys:
                if key and key not in names:
                    prio = _name_priority({"layout": p.layout}, is_face) - _LOCALIZED_PRIORITY_OFFSET
                    votes.setdefault(key, Counter())[(prio, p.oracle_id, is_face, p.lang)] += 1
        for key, counter in votes.items():
            names[key] = max(counter.items(), key=lambda kv: (kv[0][0], kv[1]))[0]

        conn.executemany(
            "INSERT INTO card_name VALUES (?, ?, ?, ?)",
            [(k, v[1], v[2], v[3]) for k, v in names.items()],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO db_metadata VALUES (?, ?)",
            [("bulk_updated_at", bulk_updated_at), ("sets_updated_at", sets_updated_at),
             ("schema_version", SCHEMA_VERSION), ("printed_langs", ",".join(sorted(set(printed_langs)))),
             ("localized_prints", str(localized_count)), ("localized_names", str(len(votes))),
             ("sets_fingerprint", sets_fingerprint(sets)),
             # para a estimativa de tempo da próxima atualização (ETA no app)
             ("default_cards_lines", str(lines)), ("build_seconds", f"{time.time() - started:.1f}"),
             ("build_machine", platform.machine())],
        )
        conn.commit()
        conn.execute("VACUUM")
    except BaseException:
        conn.close()
        dest.unlink(missing_ok=True)          # cancelado ou falhou: nada de banco pela metade
        raise
    else:
        conn.close()
    return prints, len(names)


def _mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n else "? MB"


def _progress(chunks: Iterable[bytes], total: int, label: str, step: float = 0.1,
              report: Optional[Callable[[str, int, int], None]] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> Iterator[bytes]:
    """Repassa os pedaços do download e registra o andamento a cada 10 % no log (o all_cards leva minutos
    no Pi). report(etapa, bytes, total): a cada pedaço, para quem acompanha (barra de progresso no app).
    should_stop(): True = SyncCancelled."""
    done, mark, t0 = 0, step, time.time()
    for chunk in chunks:
        if should_stop is not None and should_stop():
            raise SyncCancelled()
        done += len(chunk)
        if report is not None:
            report(label, done, total)
        if total and done >= mark * total:
            log.info("%s: %3.0f%% (%s, %.0f s)", label, 100 * done / total, _mb(done), time.time() - t0)
            while mark * total <= done:
                mark += step
        yield chunk


class ScryfallSyncService:
    """
    Atualiza o espelho local do Scryfall (CV-SD-04). Só deve ser chamado quando nenhuma run está ativa.
    O banco é construído num arquivo temporário e substitui o cards.db de forma atômica.
    `printed_langs`: idiomas cujos nomes impressos também são baixados (all_cards); vazio = só inglês.
    """

    def __init__(self, db_path: str | os.PathLike, timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
                 printed_langs: Iterable[str] = DEFAULT_PRINTED_LANGS,
                 max_age_days: Optional[float] = DEFAULT_MAX_AGE_DAYS,
                 on_progress: Optional[Callable[[str, int, int], None]] = None,
                 should_stop: Optional[Callable[[], bool]] = None):
        """on_progress(etapa, feito, total): "checking", "all_cards" / "default_cards" (bytes), "building"
        (cartas lidas / esperadas), "finishing". should_stop(): True cancela (SyncCancelled), sem tocar
        no cards.db."""
        self.db_path = Path(db_path)
        self.timeout = timeout
        self.printed_langs = tuple(sorted({lang.strip().lower() for lang in printed_langs if lang.strip()}))
        self.max_age_days = max_age_days
        self.on_progress = on_progress
        self.should_stop = should_stop

    def _report(self, stage: str, done: int = 0, total: int = 0) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, done, total)

    def _get(self, url: str, **kw):
        import requests  # importado aqui: a identificação nunca precisa de rede (RNF15)
        r = requests.get(url, headers=HEADERS, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def fetch_bulk_entries(self) -> dict[str, BulkEntry]:
        """Índice dos bulks da Scryfall (uma requisição): tipo -> BulkEntry."""
        entries = {}
        for entry in self._get(f"{SCRYFALL_API}/bulk-data").json().get("data", []):
            if entry.get("type") and entry.get("jsonl_download_uri"):
                entries[entry["type"]] = BulkEntry(entry.get("updated_at", ""), entry["jsonl_download_uri"],
                                                   int(entry.get("compressed_size") or entry.get("size") or 0))
        return entries

    def fetch_bulk_index(self, kind: str = "default_cards") -> BulkEntry:
        entry = self.fetch_bulk_entries().get(kind)
        if entry is None:
            raise RuntimeError(f"Scryfall bulk entry '{kind}' not found")
        return entry

    def local_print_count(self) -> int:
        """Impressões no cards.db local (estimativa do tamanho do default_cards para o andamento)."""
        try:
            conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                return int(conn.execute("SELECT COUNT(*) FROM card_print").fetchone()[0])
            finally:
                conn.close()
        except sqlite3.Error:
            return 0

    def fetch_sets(self) -> list[dict]:
        sets, url = [], f"{SCRYFALL_API}/sets"
        while url:
            page = self._get(url).json()
            sets.extend(page.get("data", []))
            url = page.get("next_page") if page.get("has_more") else None
        return sets

    def local_metadata(self) -> dict[str, str]:
        if not self.db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                return dict(conn.execute("SELECT key, value FROM db_metadata"))
            finally:
                conn.close()
        except sqlite3.Error:
            return {}

    def local_bulk_updated_at(self) -> Optional[str]:
        return self.local_metadata().get("bulk_updated_at")

    def check(self) -> SyncCheck:
        """
        O cards.db local tem as mesmas cartas que a Scryfall? Duas requisições pequenas (índice dos
        bulks e lista de edições); exceções de rede (requests) sobem para quem chamou.
        """
        entries = self.fetch_bulk_entries()
        bulk = entries.get("default_cards")
        if bulk is None:
            raise RuntimeError("Scryfall bulk entry 'default_cards' not found")
        localized_bulk = None
        if self.printed_langs:
            localized_bulk = entries.get("all_cards")
            if localized_bulk is None:
                raise RuntimeError("Scryfall bulk entry 'all_cards' not found")
        sets = self.fetch_sets()
        fingerprint = sets_fingerprint(sets)
        meta = self.local_metadata()
        langs = ",".join(self.printed_langs)
        if not meta:
            reason = "no local card database"
        elif meta.get("schema_version") != SCHEMA_VERSION:
            reason = f"database schema {meta.get('schema_version')} -> {SCHEMA_VERSION}"
        elif meta.get("printed_langs", "") != langs:
            reason = f"printed-name languages '{meta.get('printed_langs', '')}' -> '{langs}'"
        elif meta.get("sets_fingerprint") != fingerprint:
            reason = "new cards or sets on Scryfall"
        elif self._too_old(meta.get("bulk_updated_at", ""), bulk.updated_at):
            reason = f"local cards older than {self.max_age_days:g} days"
        else:
            return SyncCheck(True, "up to date", bulk, sets, fingerprint, localized_bulk)
        return SyncCheck(False, reason, bulk, sets, fingerprint, localized_bulk)

    def _too_old(self, local: str, remote: str) -> bool:
        if not self.max_age_days:
            return False
        local_t, remote_t = _parse_time(local), _parse_time(remote)
        if local_t is None or remote_t is None:
            return local != remote
        return (remote_t - local_t).total_seconds() > self.max_age_days * 86400

    def download_localized(self, bulk: Optional[BulkEntry] = None) -> list[LocalizedPrint]:
        """Nomes impressos dos idiomas pedidos, lidos do all_cards em streaming (nada vai para o disco)."""
        if not self.printed_langs:
            return []
        bulk = bulk or self.fetch_bulk_index("all_cards")
        langs = ",".join(self.printed_langs)
        log.info("streaming Scryfall all_cards (%s) for the %s printed names...", _mb(bulk.size), langs)
        with self._get(bulk.download_uri, stream=True) as r:
            total = bulk.size or int(r.headers.get("Content-Length") or 0)
            chunks = _progress(r.iter_content(chunk_size=1 << 16), total, "all_cards", report=self.on_progress,
                               should_stop=self.should_stop)
            found = list(localized_prints(iter_jsonl_chunks(chunks, keep=lang_filter(self.printed_langs)),
                                          self.printed_langs))
        log.info("all_cards: %d %s prints with a printed name", len(found), langs)
        return found

    def sync(self, force: bool = False, before_replace: Optional[Callable[[], None]] = None,
             check: Optional[SyncCheck] = None) -> SyncReport:
        """
        `before_replace` é chamado logo antes da troca atômica do arquivo
        (o VisionModule usa-o para fechar a conexão aberta — necessário no Windows).
        `check`: o resultado de um check() recém-feito (evita repetir as requisições).
        """
        t0 = time.time()
        if check is None:
            self._report("checking")
            check = self.check()
        bulk, sets = check.bulk, check.sets
        if not force and check.current:
            return SyncReport(updated=False, bulk_updated_at=bulk.updated_at, printed_langs=self.printed_langs)
        log.info("updating the card database: %s", "forced" if force and check.current else check.reason)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_db = self.db_path.with_name(self.db_path.name + ".tmp")

        meta = self.local_metadata()
        expected_cards = int(meta.get("default_cards_lines") or 0) or self.local_print_count() or 112_000
        # o download longo primeiro: se falhar, nada foi construído
        localized = self.download_localized(check.localized_bulk)

        log.info("downloading Scryfall default_cards (%s)...", _mb(bulk.size))
        with tempfile.TemporaryFile() as gz:
            with self._get(bulk.download_uri, stream=True) as r:
                for chunk in _progress(r.iter_content(chunk_size=1 << 16), bulk.size, "default_cards", 0.25,
                                       report=self.on_progress, should_stop=self.should_stop):
                    gz.write(chunk)
            gz.seek(0)
            log.info("building %s...", tmp_db)
            self._report("building", 0, expected_cards)
            prints, names = build_database(iter_jsonl(gz), sets, tmp_db,
                                           bulk_updated_at=bulk.updated_at,
                                           sets_updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                           localized=localized, printed_langs=self.printed_langs,
                                           on_card=lambda n: self._report("building", n, max(n, expected_cards)),
                                           should_stop=self.should_stop)
        conn = sqlite3.connect(tmp_db)
        try:
            meta = dict(conn.execute("SELECT key, value FROM db_metadata"))
        finally:
            conn.close()

        if self.should_stop is not None and self.should_stop():
            tmp_db.unlink(missing_ok=True)
            raise SyncCancelled()
        self._report("finishing")
        if before_replace:
            before_replace()
        os.replace(tmp_db, self.db_path)
        return SyncReport(True, prints, names, len(sets), bulk.updated_at, time.time() - t0,
                          printed_langs=self.printed_langs,
                          localized_prints=int(meta.get("localized_prints") or 0),
                          localized_names=int(meta.get("localized_names") or 0))
