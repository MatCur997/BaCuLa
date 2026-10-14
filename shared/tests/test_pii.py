import pytest

from bacula_shared.pii import PiiDetector, Pseudonymizer


@pytest.fixture
def pseudo():
    return Pseudonymizer(PiiDetector(use_spacy=False))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Mi chiamo Mario Rossi e vorrei prenotare", "Mi chiamo [NOME_1] e vorrei prenotare"),
        ("mi chiamo mario rossi, grazie", "mi chiamo [NOME_1], grazie"),
        ("Prenota a nome di Bianchi per 4 persone", "Prenota a nome di [NOME_1] per 4 persone"),
        ("a nome Rossi alle 20", "a nome [NOME_1] alle 20"),
        ("Buonasera, sono Giulia D'Angelo.", "Buonasera, sono [NOME_1]."),
        ("il mio nome è Luca", "il mio nome è [NOME_1]"),
        ("nome: Anna Verdi", "nome: [NOME_1]"),
        ("Sono la signora Esposito", "Sono la signora [NOME_1]"),
        ("Il mio numero è 333 123 4567", "Il mio numero è [TELEFONO_1]"),
        ("chiamatemi al +39 347-1234567", "chiamatemi al [TELEFONO_1]"),
        ("fisso 02 1234567", "fisso [TELEFONO_1]"),
        ("dettato: 3 3 3 1 2 3 4 5 6 7", "dettato: [TELEFONO_1]"),
        ("scrivete a mario.rossi@example.com", "scrivete a [EMAIL_1]"),
        ("CF RSSMRA80A01H501U", "CF [CODICE_FISCALE_1]"),
        ("carta 4111 1111 1111 1111", "carta [CARTA_1]"),
        ("IBAN IT60X0542811101000000123456", "IBAN [IBAN_1]"),
    ],
)
def test_redact(pseudo, text, expected):
    assert pseudo.redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Sono 4 persone, sabato alle 20:30",
        "vorrei modificare la prenotazione 482157",
        "sono disponibile domani a pranzo",
        "qual è il nome del vostro piatto migliore?",
        "il 20/10/2026 alle 13.00 per 6",
        "Quanto costa la carbonara? 12 euro?",
    ],
)
def test_no_false_positives(pseudo, text):
    assert pseudo.redact(text) == text


def test_same_value_same_token_across_turns(pseudo):
    assert pseudo.redact("sono Mario") == "sono [NOME_1]"
    assert pseudo.redact("Mi chiamo Luca") == "Mi chiamo [NOME_2]"
    assert pseudo.redact("ripeto, sono Mario") == "ripeto, sono [NOME_1]"
    assert pseudo.redact("tel 3331234567") == pseudo.redact("tel +39 333 123 4567")


def test_restore(pseudo):
    pseudo.redact("Mi chiamo Mario Rossi, cell 3331234567")
    reply = "Grazie [NOME_1], ti scriveremo al [TELEFONO_1]. [NOME_9] resta invariato."
    assert pseudo.restore(reply) == "Grazie Mario Rossi, ti scriveremo al 3331234567. [NOME_9] resta invariato."
