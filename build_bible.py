"""Normalize full public-domain Bible texts into Heard's corpus format.

Source: scrollmapper/bible_databases (public domain translations).
Output: data/bible/<CODE>.jsonl  - one verse per line, {ref, book, ch, v, text}

Translations shipped:
  KJV  King James Version (1769)        - traditional English, 66 books
  BSB  Berean Standard Bible             - modern English, 66 books
  DRC  Douay-Rheims (Challoner)          - Catholic canon, 73+ books,
                                           includes the deuterocanonical books

Run:  python build_bible.py            (downloads if needed, then normalizes)
"""
import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "bible"
RAW = HERE / "data" / "_raw"
BASE = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/json"

TRANSLATIONS = {
    "KJV": {"label": "King James Version (1769)", "canon": "protestant"},
    "BSB": {"label": "Berean Standard Bible", "canon": "protestant"},
    "DRC": {"label": "Douay-Rheims, Challoner Revision", "canon": "catholic"},
}


def download(code):
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / f"{code}.json"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    url = f"{BASE}/{code}.json"
    print(f"  downloading {code} from {url}")
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def normalize(code, src):
    data = json.loads(src.read_text(encoding="utf-8"))
    rows = []
    for book in data.get("books", []):
        bname = book["name"]
        for chapter in book.get("chapters", []):
            cnum = chapter.get("chapter")
            for verse in chapter.get("verses", []):
                text = " ".join(str(verse.get("text", "")).split())
                if not text:
                    continue
                vnum = verse.get("verse")
                rows.append({
                    "ref": f"{bname} {cnum}:{vnum}",
                    "book": bname,
                    "ch": int(cnum),
                    "v": int(vnum),
                    "text": text,
                })
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for code, meta in TRANSLATIONS.items():
        print(f"{code}: {meta['label']}")
        src = download(code)
        rows = normalize(code, src)
        dest = OUT / f"{code}.jsonl"
        with dest.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        books = []
        seen = set()
        for row in rows:
            if row["book"] not in seen:
                seen.add(row["book"])
                books.append(row["book"])
        manifest[code] = dict(meta, verses=len(rows), books=len(books),
                              file=f"{code}.jsonl", book_names=books)
        size_mb = dest.stat().st_size / 1e6
        print(f"  -> {dest.name}  verses={len(rows)}  books={len(books)}  {size_mb:.1f} MB")
    (OUT / "manifest.json").write_text(
        json.dumps({
            "source": "https://github.com/scrollmapper/bible_databases",
            "license": "public domain translations",
            "translations": manifest,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
