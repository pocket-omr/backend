"""Field post-processing: correct look-alike characters per field type.

Names are alphabetic, so any digit the model emitted there is mapped to its
look-alike letter; group / registration_number are numeric, so letters are
mapped to look-alike digits. This is applied to each recognised field before
fuzzy-matching against the roster, and the roster names are normalised the same
way (uppercase, no spaces/dashes) so the two sides line up.
"""

# Look-alike letter -> digit (for numeric fields: group, registration_number)
LETTER_TO_DIGIT = {
    "A": "4", "B": "8", "C": "0", "D": "0", "E": "3", "F": "7", "G": "6",
    "H": "4", "I": "1", "J": "1", "K": "4", "L": "1", "M": "0", "N": "1",
    "O": "0", "P": "9", "Q": "0", "R": "2", "S": "5", "T": "7", "U": "0",
    "V": "4", "W": "0", "X": "8", "Y": "7", "Z": "2",
}

# Look-alike digit -> letter (for name fields: first_name, last_name)
DIGIT_TO_LETTER = {
    "0": "O", "1": "I", "2": "Z", "3": "B", "4": "A",
    "5": "S", "6": "G", "7": "T", "8": "B", "9": "G",
}

_NUMERIC_FIELDS = {"group", "registration_number"}
_NAME_FIELDS = {"first_name", "last_name"}


def postprocess_field(text, field_name):
    """Clean a recognised field string for matching.

    - group / registration_number: keep digits; map look-alike letters to
      digits; drop everything else.
    - first_name / last_name: uppercase; drop spaces and dashes; map look-alike
      digits to letters; keep letters only.
    """
    text = text or ""
    if field_name in _NUMERIC_FIELDS:
        out = []
        for ch in text:
            if ch.isdigit():
                out.append(ch)
            elif ch.upper() in LETTER_TO_DIGIT:
                out.append(LETTER_TO_DIGIT[ch.upper()])
            # else: drop
        return "".join(out)

    if field_name in _NAME_FIELDS:
        out = []
        for ch in text:
            if ch in (" ", "-"):
                continue
            up = ch.upper()
            if up.isalpha():
                out.append(up)
            elif ch in DIGIT_TO_LETTER:
                out.append(DIGIT_TO_LETTER[ch])
            # else: drop
        return "".join(out)

    # Unknown field: return uppercased, trimmed text untouched.
    return text.strip().upper()
