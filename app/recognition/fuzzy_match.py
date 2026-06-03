"""Fuzzy roster matching via weighted normalised Levenshtein distance.

Refactored from the notebook's ``fuzzy_match.py``: the import-time module-global
``df`` / ``GT_CATALOGUE`` is gone. The catalogue is now built explicitly from a
DataFrame via :func:`build_catalogue`, and matching is a pure function
(:func:`match`). ``levenshtein`` / ``lev_norm`` are unchanged.

For each predicted ``(first_name, last_name, group, registration_number)`` tuple
we find the closest roster record. Lower score = better; ``registration_number``
(0.45) dominates because it is the most discriminative field.
"""


def levenshtein(a, b):
    if len(a) < len(b):
        return levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def lev_norm(a, b):
    """Levenshtein normalised to [0,1]. 0=identical, 1=completely different."""
    return levenshtein(a, b) / max(len(a), len(b), 1)


# Field weights (information-theoretic, tuned to dataset):
#   registration_number: 0.45  (most discriminative — long numeric id)
#   first_name / last_name: 0.25 each
#   group: 0.05  (low entropy)
MATCH_WEIGHTS = {
    "first_name": 0.25,
    "last_name": 0.25,
    "group": 0.05,
    "registration_number": 0.45,
}

# Columns required on the roster (after header normalisation). The student's
# registration_number is the unique identifier returned as the match id.
CATALOGUE_FIELDS = ["first_name", "last_name", "group", "registration_number"]


def _norm_name(value):
    """Match how ``postprocess_field`` normalises name fields."""
    return str(value).upper().replace(" ", "").replace("-", "")


def build_catalogue(df):
    """Build the roster catalogue from a DataFrame.

    Expects the (already header-normalised) columns in ``CATALOGUE_FIELDS``.
    Names are uppercased with spaces and dashes removed so they line up with
    ``postprocess_field``; group/registration_number are kept as strings
    (read with ``dtype=str`` upstream to preserve leading zeros).
    """
    catalogue = []
    for _, row in df.iterrows():
        catalogue.append(
            {
                "first_name": _norm_name(row["first_name"]),
                "last_name": _norm_name(row["last_name"]),
                "group": str(row["group"]),
                "registration_number": str(row["registration_number"]),
            }
        )
    return catalogue


def match(pred_fields, catalogue, weights=MATCH_WEIGHTS, top_k=1):
    """Return the ``top_k`` closest records to ``pred_fields``.

    Result is a list of ``(score, record)`` sorted ascending (0 = perfect).
    Empty if the catalogue is empty.
    """
    if not catalogue:
        return []
    scores = []
    for rec in catalogue:
        score = sum(weights[f] * lev_norm(pred_fields.get(f, ""), rec[f]) for f in weights)
        scores.append((score, rec))
    scores.sort(key=lambda x: x[0])
    return scores[:top_k]
