"""Checksums used by both the data generator and the runtime validators (no dependencies)."""

_V_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_V_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 7, 6, 0, 8], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_V_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(digits):
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[(i + 1) % 8][int(d)]]
    return str(_V_INV[c])


def verhoeff_valid(digits):
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[i % 8][int(d)]]
    return c == 0


def luhn_check_digit(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def luhn_valid(digits):
    return luhn_check_digit(digits[:-1]) == digits[-1]


def _iban_mod97(iban):
    s = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in s)) % 97


def iban_check_digits(country, bban):
    """Two check digits for an IBAN (ISO 13616 mod-97)."""
    return f"{98 - _iban_mod97(country + '00' + bban):02d}"


def iban_valid(iban):
    s = iban.replace(" ", "").upper()
    return 15 <= len(s) <= 34 and s[:2].isalpha() and s[2:4].isdigit() and s.isalnum() and _iban_mod97(s) == 1
