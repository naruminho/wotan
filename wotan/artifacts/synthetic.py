"""Seeded synthetic dataset generator on Faker (the ``faker`` extra).

One schema call produces realistic tabular data: names, e-mails, documents
(CPF/CNPJ with VALID check digits), phones, addresses, dates, numbers and
categories. Everything is deterministic under ``seed`` - the same seed always
generates the same rows, which makes datasets reproducible and testable.

Field spec: ``{name: "type"}``, ``{name: {"type": ..., "min": ..., "max": ...,
"choices": [...], "format": ...}}``. Locales: ``pt_BR`` (default), ``en_US``...
"""

from __future__ import annotations

import random
import statistics
from typing import Any

_FAKER_SINGLETONS: dict[str, Any] = {}


def _faker(locale: str):
    try:
        import faker
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"faker is required for data_synthetic ({exc})") from exc
    key = locale or "pt_BR"
    if key not in _FAKER_SINGLETONS:
        _FAKER_SINGLETONS[key] = faker.Faker(key)
    return _FAKER_SINGLETONS[key]


def _cpf(rng: random.Random, formatted: bool = True) -> str:
    digits = [rng.randint(0, 9) for _ in range(9)]
    d1 = (sum(d * w for d, w in zip(digits, range(10, 1, -1))) * 10) % 11 % 10
    digits.append(d1)
    d2 = (sum(d * w for d, w in zip(digits, range(11, 1, -1))) * 10) % 11 % 10
    digits.append(d2)
    s = "".join(str(d) for d in digits)
    if formatted:
        return f"{s[:3]}.{s[3:6]}.{s[6:9]}-{s[9:]}"
    return s


def _cnpj(rng: random.Random, formatted: bool = True) -> str:
    def calc(base: list[int], weights: list[int]) -> int:
        total = sum(d * w for d, w in zip(base, weights))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    base = [rng.randint(0, 9) for _ in range(12)]
    d1 = calc(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    base.append(d1)
    d2 = calc(base, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    s = "".join(str(d) for d in base) + str(d2)
    if formatted:
        return f"{s[:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:]}"
    return s


TYPES = {
    "name", "first_name", "last_name", "email", "company", "job",
    "cpf", "cnpj", "rg", "phone", "cellphone", "cep", "address", "street",
    "city", "state", "country", "neighborhood",
    "date", "datetime", "date_this_year", "date_range",
    "int", "number", "money", "bool", "choice", "uuid", "word", "sentence",
    "paragraph", "url", "ip", "latitude", "longitude", "cpf_cnpj",
}


def generate_rows(schema: dict[str, Any], rows: int, *, seed: int = 42,
                  locale: str = "pt_BR") -> list[list[Any]]:
    """Generate a table (header + rows) from a schema mapping. Deterministic."""
    if not schema:
        raise ValueError("schema is empty: pass at least one field, e.g. {\"nome\": \"name\"}")
    rows = max(1, min(int(rows), 100_000))
    fk = _faker(locale)
    # Seed BOTH engines for full determinism (Faker uses class-level state).
    try:
        from faker import Faker as _Faker

        _Faker.seed(seed)
    except Exception:
        pass
    rng = random.Random(seed)
    header = list(schema.keys())
    out: list[list[Any]] = [header]

    def value_for(spec: Any) -> Any:
        opts: dict[str, Any] = {}
        ftype = spec
        if isinstance(spec, dict):
            ftype = str(spec.get("type", "word"))
            opts = spec
        ftype = ftype.strip().lower()

        if ftype == "name":
            return fk.name()
        if ftype == "first_name":
            return fk.first_name()
        if ftype == "last_name":
            return fk.last_name()
        if ftype == "email":
            return fk.email()
        if ftype == "company":
            return fk.company()
        if ftype == "job":
            return fk.job()
        if ftype == "cpf":
            return _cpf(rng, formatted=bool(opts.get("formatted", True)))
        if ftype == "cnpj":
            return _cnpj(rng, formatted=bool(opts.get("formatted", True)))
        if ftype == "cpf_cnpj":
            return _cpf(rng, bool(opts.get("formatted", True))) if rng.random() < 0.5 else _cnpj(rng, bool(opts.get("formatted", True)))
        if ftype == "rg":
            return "".join(str(rng.randint(0, 9)) for _ in range(9))
        if ftype == "phone":
            return fk.phone_number()
        if ftype == "cellphone":
            return f"({rng.choice([11, 21, 31, 41, 51, 61, 71, 81, 85])}) 9{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
        if ftype == "cep":
            return f"{rng.randint(10000, 99999)}-{rng.randint(100, 999)}"
        if ftype == "address":
            return fk.address().replace("\n", ", ")
        if ftype == "street":
            return fk.street_name()
        if ftype == "city":
            return fk.city()
        if ftype == "state":
            return fk.state_abbr() if locale.startswith("pt") else fk.state()
        if ftype == "country":
            return fk.country()
        if ftype == "neighborhood":
            return fk.neighborhood() if hasattr(fk, "neighborhood") else fk.city()
        if ftype in ("date", "date_this_year"):
            if ftype == "date_this_year":
                return fk.date_this_year().isoformat()
            return fk.date_between(start_date="-10y", end_date="today").isoformat()
        if ftype == "date_range":
            start = str(opts.get("start", "-2y"))
            end = str(opts.get("end", "today"))
            return fk.date_between(start_date=start, end_date=end).isoformat()
        if ftype == "datetime":
            return fk.date_time_this_year().strftime("%Y-%m-%d %H:%M:%S")
        if ftype == "int":
            return rng.randint(int(opts.get("min", 0)), int(opts.get("max", 100)))
        if ftype == "number":
            return round(rng.uniform(float(opts.get("min", 0.0)), float(opts.get("max", 1.0))),
                         int(opts.get("decimals", 2)))
        if ftype == "money":
            lo = float(opts.get("min", 10.0))
            hi = float(opts.get("max", 10000.0))
            return round(rng.uniform(lo, hi), 2)
        if ftype == "bool":
            return rng.random() < float(opts.get("probability", 0.5))
        if ftype == "choice":
            choices = list(opts.get("choices") or [])
            if not choices:
                raise ValueError("choice field needs 'choices': [...]")
            weights = list(opts.get("weights") or [1] * len(choices))
            return rng.choices(choices, weights=weights, k=1)[0]
        if ftype == "uuid":
            import uuid

            return str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if ftype == "word":
            return fk.word()
        if ftype == "sentence":
            return fk.sentence(nb_words=int(opts.get("words", 8)))
        if ftype == "paragraph":
            return fk.paragraph(nb_sentences=int(opts.get("sentences", 2)))
        if ftype == "url":
            return fk.url()
        if ftype == "ip":
            return fk.ipv4()
        if ftype == "latitude":
            return round(float(opts.get("min", -33.75)) + rng.random() * (float(opts.get("max", 5.27)) - float(opts.get("min", -33.75))), 6)
        if ftype == "longitude":
            return round(float(opts.get("min", -73.99)) + rng.random() * (float(opts.get("max", -32.39)) - float(opts.get("min", -73.99))), 6)
        raise ValueError(
            f"unknown field type {ftype!r} - supported: {', '.join(sorted(TYPES))}"
        )

    for _ in range(rows):
        out.append([value_for(schema[k]) for k in header])
    return out


def summarize(rows: list[list[Any]]) -> dict[str, Any]:
    """Tiny column profile (types + numeric stats) so the agent can describe data."""
    if not rows:
        return {}
    header = [str(c) for c in rows[0]]
    cols: dict[str, dict[str, Any]] = {}
    for idx, name in enumerate(header):
        values = [r[idx] for r in rows[1:] if idx < len(r) and r[idx] not in (None, "")]
        numeric = [float(v) for v in values if isinstance(v, (int, float))]
        col: dict[str, Any] = {
            "non_empty": len(values),
            "unique": len({str(v) for v in values}),
        }
        if numeric and len(numeric) >= max(2, len(values) // 2):
            col["numeric"] = {
                "min": min(numeric), "max": max(numeric),
                "mean": round(statistics.fmean(numeric), 4),
                "median": round(statistics.median(numeric), 4),
            }
        elif values:
            from collections import Counter

            top = Counter(str(v) for v in values).most_common(3)
            col["top"] = [f"{k} ({n})" for k, n in top]
        cols[name] = col
    return {"columns": cols, "data_rows": len(rows) - 1}
