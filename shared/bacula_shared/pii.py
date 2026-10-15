"""Pseudonimizzazione dei dati personali prima dell'invio al backend.

Il testo dell'utente viene ripulito sul client: ogni dato
personale è sostituito da un segnaposto come [NOME_1] o [TELEFONO_1].
La mappa 'segnaposto -> valore' rimane solamente in memoria sul client e
serve a ripristinare i valori nella risposta mostrata all'utente.

Rilevamento basato su regole:
- email, telefono, codice fiscale, IBAN, carta di credito: espressioni regolari
- nomi: frasi introduttive ("mi chiamo ...", "a nome di ...", "sono ...")
Se spaCy e il modello 'it_core_news_sm' sono installati, le entità PER
trovate dal modello si aggiungono a quelle delle regole.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Ordine = priorità in caso di sovrapposizione.
KINDS = ("EMAIL", "IBAN", "CARTA", "CODICE_FISCALE", "TELEFONO", "NOME")

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b", re.IGNORECASE)
_CODICE_FISCALE = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.IGNORECASE)
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# Cellulari (3xx) e fissi (0x) italiani, con prefisso internazionale opzionale.
_PHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\s?39[\s.-]?)?(?:3\d{2}|0\d{1,3})(?:[\s.-]?\d){5,8}(?![\w])")
# Qualsiasi sequenza di almeno 9 cifre (es. numero dettato cifra per cifra).
_LONG_NUMBER = re.compile(r"(?<![\w+])\+?\d(?:[\s.-]?\d){8,}(?![\w])")

_WORD = r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*"
# Dopo questi inneschi il nome può essere anche minuscolo.
_STRONG_TRIGGER = re.compile(
    r"\b(?:(?:mi\s+chiamo|il\s+mio\s+(?:nome|cognome)\s+(?:è|e'|e)|a\s+nome(?:\s+di)?|"
    r"intestat[ao]\s+a)(?=\s)|(?:nome\s+e\s+)?(?:nome|cognome)\s*:)\s*",
    re.IGNORECASE,
)
# Dopo questi inneschi il nome deve iniziare con la maiuscola.
_WEAK_TRIGGER = re.compile(
    r"\b(?:sono|qui\s+(?:è|e')|parla|nome|cognome|signor[ae]?|sig\.(?:ra)?|"
    r"dott\.(?:ssa)?|dottor(?:essa)?)(?=\s)\s*",
    re.IGNORECASE,
)
_STOPWORDS = {
    "a", "ad", "al", "alla", "alle", "allo", "anche", "buonasera", "buongiorno", "che", "ci",
    "ciao", "codice", "con", "cui", "da", "dal", "dalle", "di", "del", "della", "domani",
    "e", "ed", "è", "email", "gli", "grazie", "ho", "i", "il", "in", "io", "la", "le", "lo",
    "ma", "mail", "mi", "mia", "mio", "nel", "nella", "non", "nostra", "nostro", "numero",
    "oggi", "per", "persone", "posti", "prenotare", "prenotazione", "qui", "salve", "se",
    "si", "sì", "sono", "stasera", "sua", "suo", "telefono", "ti", "tavolo", "un", "una",
    "uno", "vi", "vorrei", "volevo", "voglio", "cellulare", "prego", "sera", "pranzo", "cena",
    "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica",
    "lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "disponibile", "interessato",
    "interessata", "qua", "sempre", "io", "noi", "molto", "solo", "appena", "contento",
    "contenta", "arrivato", "arrivata", "allergico", "allergica", "celiaco", "celiaca",
    "vegano", "vegana", "vegetariano", "vegetariana", "in", "senza", "tipo",
}
_MAX_NAME_WORDS = 3


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    kind: str
    value: str


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _name_after(text: str, pos: int, require_capital: bool) -> tuple[int, int] | None:
    """Cattura fino a 3 parole a partire da pos, fermandosi alle stopword."""
    word_re = re.compile(_WORD)
    start = end = None
    count = 0
    i = pos
    while count < _MAX_NAME_WORDS:
        m = word_re.match(text, i)
        if not m:
            break
        word = m.group(0)
        if word.lower() in _STOPWORDS:
            break
        if (require_capital or count > 0 and text[start].isupper()) and not word[0].isupper():
            break
        if start is None:
            start = m.start()
        end = m.end()
        count += 1
        sep = re.match(r"[ \t]+", text[end:])
        if not sep:
            break
        i = end + sep.end()
    if start is None:
        return None
    return start, end


class PiiDetector:
    """Trova i dati personali in un testo."""

    def __init__(self, use_spacy: bool = True):
        self._nlp = None
        if use_spacy:
            try:  # opzionale: migliora il riconoscimento dei nomi
                import spacy

                self._nlp = spacy.load("it_core_news_sm")
            except Exception:
                self._nlp = None

    @property
    def uses_spacy(self) -> bool:
        return self._nlp is not None

    def find(self, text: str) -> list[Match]:
        found: list[Match] = []

        def add(start: int, end: int, kind: str) -> None:
            found.append(Match(start, end, kind, text[start:end]))

        for m in _EMAIL.finditer(text):
            add(m.start(), m.end(), "EMAIL")
        for m in _IBAN.finditer(text):
            if any(c.isdigit() for c in m.group(0)[4:]) and any(c.isalpha() for c in m.group(0)[:2]):
                add(m.start(), m.end(), "IBAN")
        for m in _CARD.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                add(m.start(), m.end(), "CARTA")
        for m in _CODICE_FISCALE.finditer(text):
            add(m.start(), m.end(), "CODICE_FISCALE")
        for m in _PHONE.finditer(text):
            add(m.start(), m.end(), "TELEFONO")
        for m in _LONG_NUMBER.finditer(text):
            add(m.start(), m.end(), "TELEFONO")

        for trigger, require_capital in ((_STRONG_TRIGGER, False), (_WEAK_TRIGGER, True)):
            for m in trigger.finditer(text):
                span = _name_after(text, m.end(), require_capital)
                if span:
                    add(span[0], span[1], "NOME")

        if self._nlp is not None:
            for ent in self._nlp(text).ents:
                if ent.label_ == "PER":
                    add(ent.start_char, ent.end_char, "NOME")

        return _resolve_overlaps(found)


def _resolve_overlaps(matches: list[Match]) -> list[Match]:
    """A parità di sovrapposizione vince il tipo più prioritario, poi il più lungo."""
    ordered = sorted(matches, key=lambda m: (KINDS.index(m.kind), -(m.end - m.start)))
    kept: list[Match] = []
    for m in ordered:
        if all(m.end <= k.start or m.start >= k.end for k in kept):
            kept.append(m)
    return sorted(kept, key=lambda m: m.start)


_TOKEN = re.compile(r"\[(?:" + "|".join(KINDS) + r")_\d+\]")


class Pseudonymizer:
    """Sostituisce i dati personali con segnaposto stabili per tutta la conversazione.

    Lo stesso valore riceve sempre lo stesso segnaposto, così l'LLM può
    riferirsi a "[NOME_1]" in turni diversi.
    """

    def __init__(self, detector: PiiDetector | None = None):
        self._detector = detector or PiiDetector()
        self._token_by_key: dict[tuple[str, str], str] = {}
        self._value_by_token: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    @staticmethod
    def _key(kind: str, value: str) -> tuple[str, str]:
        if kind in ("TELEFONO", "CARTA", "IBAN"):
            norm = re.sub(r"[^0-9A-Za-z]", "", value).upper()
            if kind == "TELEFONO":
                norm = re.sub(r"^(?:\+|00)?39(?=\d{9,})", "", norm)
            return kind, norm
        return kind, " ".join(value.lower().split())

    def _token_for(self, kind: str, value: str) -> str:
        key = self._key(kind, value)
        token = self._token_by_key.get(key)
        if token is None:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            token = f"[{kind}_{self._counters[kind]}]"
            self._token_by_key[key] = token
            self._value_by_token[token] = value
        return token

    def redact(self, text: str) -> str:
        """Restituisce il testo con i dati personali sostituiti da segnaposto."""
        out = text
        for m in reversed(self._detector.find(text)):
            out = out[: m.start] + self._token_for(m.kind, m.value) + out[m.end :]
        return out

    def restore(self, text: str) -> str:
        """Rimette i valori reali al posto dei segnaposto (solo lato client)."""
        return _TOKEN.sub(lambda m: self._value_by_token.get(m.group(0), m.group(0)), text)

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._value_by_token)
