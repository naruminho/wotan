"""Artifact generation toolkit.

Real, self-verifiable documents and data files built on the well-established
Python libraries (declared as the optional ``artifacts`` extra in
``pyproject.toml`` - ``pip install -e ".[artifacts]"``):

- :mod:`.pdf`       - PDF via ReportLab (headings, tables, images, page numbers)
- :mod:`.docx`      - Word via python-docx
- :mod:`.xlsx`      - Excel via openpyxl (typed cells, formulas, filters)
- :mod:`.pptx`      - PowerPoint via python-pptx (rich slides, images, notes)
- :mod:`.csvw`      - CSV / markdown tables (standard library only)
- :mod:`.synthetic` - seeded synthetic datasets via Faker (pt-BR aware)
- :mod:`.charts`    - chart PNGs via Pillow (bar/line/pie/scatter/...)
- :mod:`.images`    - image transforms + satellite tile mosaic (config-guarded)
- :mod:`.readers`   - text extraction used by the ``doc_read`` tool so the
                      agent can verify its own generated artifacts
- :mod:`.common`    - markdown-lite parser shared by the document writers

Every module imports its library lazily and maps a missing library to a clear
ERROR / WHY / HOW TO FIX message - nothing crashes the agent turn.
"""

from __future__ import annotations


def library_missing(module: str, extra: str = "artifacts") -> dict[str, str]:
    """Standard error payload when an optional library is absent."""
    return {
        "status": "error",
        "error": f"optional library {module!r} is not installed",
        "why": f"artifact generation for this format needs the {extra} extra",
        "how_to_fix": f"pip install -e \".[{extra}]\" (or pip install {module}) and retry",
    }


from . import common  # noqa: E402  (shared parser, no third-party deps)

__all__ = ["common", "library_missing"]
