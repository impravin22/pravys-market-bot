"""One-off: upload the CAN SLIM playbook PDF to Gemini Files and print its URI.

The Worker doesn't have a filesystem, so we upload the PDF once from a
developer laptop (or CI step) and reference it in Gemini requests by its
``file_uri``. Gemini keeps uploaded files for 48 hours by default; we'll
re-upload as part of the deploy pipeline if this ever becomes an issue.

Usage::

    GOOGLE_API_KEY=... python scripts/upload_playbook.py

Output is the ``fileData.fileUri`` to paste into ``worker/wrangler.toml``
(``CANSLIM_PLAYBOOK_FILE_ID``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google import genai

DEFAULT_PDF = Path("canslim-playbook.pdf")


def main() -> int:
    pdf_path = Path(os.getenv("CANSLIM_PLAYBOOK_PATH", str(DEFAULT_PDF))).resolve()
    if not pdf_path.exists():
        print(f"ERROR: playbook not found at {pdf_path}", file=sys.stderr)
        return 1

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY is required", file=sys.stderr)
        return 1

    client = genai.Client(api_key=api_key)
    file_handle = client.files.upload(
        file=str(pdf_path),
        config={"display_name": "pravy-canslim-playbook"},
    )
    # SDK returns an object with `.uri` + `.name` — print the URI so it can
    # be stored directly as the Worker's `CANSLIM_PLAYBOOK_FILE_ID`.
    print(file_handle.uri)
    return 0


if __name__ == "__main__":
    sys.exit(main())
