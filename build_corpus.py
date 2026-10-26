"""Fetch public-domain scripture (WEB) for a curated struggle-indexed corpus.
Run once: python build_corpus.py  -> data/verses.json
Source: bible-api.com, World English Bible (public domain).
"""
import json, time, urllib.parse, os, sys
import requests

THEMES = {
  "temptation": ["1 Corinthians 10:13","James 1:12-15","Matthew 26:41","Hebrews 4:15-16","1 Peter 5:8-9","Psalm 119:11","Romans 6:12-14","2 Timothy 2:22"],
  "anger": ["James 1:19-20","Ephesians 4:26-27","Proverbs 15:1","Proverbs 29:11","Colossians 3:8","Psalm 37:8","Matthew 5:21-24","Romans 12:19"],
  "lust_purity": ["Matthew 5:27-28","1 Thessalonians 4:3-5","1 Corinthians 6:18-20","Job 31:1","Philippians 4:8","Galatians 5:16","2 Timothy 2:22","Psalm 51:10"],
  "greed_money": ["1 Timothy 6:6-10","Hebrews 13:5","Matthew 6:19-21","Luke 12:15","Proverbs 11:28","Ecclesiastes 5:10","Matthew 6:24","Proverbs 30:8-9"],
  "envy": ["Proverbs 14:30","Galatians 5:26","James 3:14-16","Psalm 73:2-3","1 Corinthians 13:4","Exodus 20:17","Romans 12:15","1 Peter 2:1"],
  "pride": ["Proverbs 16:18","James 4:6-10","Philippians 2:3-8","Romans 12:3","1 Peter 5:5-6","Proverbs 11:2","Galatians 6:3","Micah 6:8"],
  "lying_integrity": ["Proverbs 12:22","Ephesians 4:25","Colossians 3:9-10","Psalm 15:1-4","Proverbs 10:9","Zechariah 8:16","Luke 16:10","Proverbs 19:1"],
  "gossip": ["Proverbs 16:28","James 4:11","Ephesians 4:29","Proverbs 11:13","Psalm 141:3","Titus 3:2","Proverbs 26:20","1 Peter 3:10"],
  "addiction": ["1 Corinthians 6:12","Romans 7:15-25","Galatians 5:1","2 Corinthians 12:9-10","Psalm 40:1-3","1 Corinthians 10:13","John 8:34-36","Philippians 4:13"],
  "anxiety_fear": ["Philippians 4:6-7","1 Peter 5:7","Matthew 6:25-34","Isaiah 41:10","Psalm 55:22","John 14:27","Joshua 1:9","Psalm 94:19"],
  "loneliness": ["Hebrews 13:5","Psalm 68:6","Deuteronomy 31:6","Psalm 25:16-18","Matthew 28:20","Romans 8:38-39","1 Kings 19:9-12","Psalm 139:7-10"],
  "forgiveness": ["Ephesians 4:31-32","Matthew 6:14-15","Colossians 3:13","Matthew 18:21-22","Luke 6:37","Mark 11:25","Romans 12:17-21","1 John 1:9"],
  "bitterness": ["Hebrews 12:14-15","Ephesians 4:31","Romans 12:19","Job 21:25","Proverbs 14:10","Psalm 73:21-26","Acts 8:23","James 3:14"],
  "work_integrity": ["Colossians 3:23-24","Proverbs 11:1","Luke 16:10","Ephesians 6:5-8","Proverbs 13:11","1 Thessalonians 4:11-12","Titus 2:7-8","Daniel 6:4"],
  "relationships": ["1 Corinthians 13:4-7","Ephesians 5:21","Proverbs 27:17","Romans 12:10","1 John 4:7-8","Philippians 2:4","Hebrews 10:24-25","Ecclesiastes 4:9-12"],
  "doubt": ["Mark 9:24","James 1:5-8","John 20:24-29","Psalm 13:1-6","Hebrews 11:1","Isaiah 55:8-9","Jude 1:22","Matthew 11:2-6"],
  "despair": ["Psalm 34:17-18","Psalm 42:5-11","2 Corinthians 4:8-9","Lamentations 3:19-24","Isaiah 43:1-3","Romans 8:28","Psalm 23:4","Revelation 21:4"],
  "patience": ["Romans 12:12","Galatians 6:9","James 5:7-8","Psalm 27:14","Ecclesiastes 7:8","Colossians 1:11","Isaiah 40:31","Romans 5:3-5"],
  "humility": ["Micah 6:8","Philippians 2:3-4","Proverbs 22:4","Matthew 23:12","1 Peter 5:5","James 4:10","Luke 14:11","Romans 12:16"],
  "self_control": ["Galatians 5:22-23","Proverbs 25:28","2 Peter 1:5-7","1 Corinthians 9:24-27","Titus 2:11-12","2 Timothy 1:7","Romans 13:14","1 Thessalonians 5:6-8"],
}

def fetch(ref, translation="web", tries=3):
    url = f"https://bible-api.com/{urllib.parse.quote(ref)}?translation={translation}"
    for a in range(tries):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            pass
        time.sleep(1.2 * (a + 1))
    return None

def main():
    out, missing = [], []
    seen = set()
    for theme, refs in THEMES.items():
        for ref in refs:
            data = fetch(ref)
            if not data or "verses" not in data:
                missing.append((theme, ref)); print("MISS", theme, ref); continue
            text = " ".join(v["text"].replace("\n", " ").strip() for v in data["verses"])
            text = " ".join(text.split())
            canonical = data.get("reference", ref)
            key = canonical.lower()
            if key in seen:
                for rec in out:
                    if rec["ref"].lower() == key and theme not in rec["themes"]:
                        rec["themes"].append(theme)
                continue
            seen.add(key)
            out.append({
                "ref": canonical,
                "book": data["verses"][0]["book_name"],
                "chapter": data["verses"][0]["chapter"],
                "themes": [theme],
                "text": text,
                "translation": "WEB",
            })
            print("ok", canonical, f"({len(text)} ch)")
            time.sleep(0.35)
    os.makedirs("data", exist_ok=True)
    with open("data/verses.json", "w", encoding="utf-8") as f:
        json.dump({"translation": "World English Bible (public domain)",
                   "source": "https://bible-api.com",
                   "themes": sorted(THEMES.keys()),
                   "verses": out}, f, indent=1, ensure_ascii=False)
    print(f"\nWROTE data/verses.json  verses={len(out)} missing={len(missing)}")
    if missing: print("MISSING:", missing)

if __name__ == "__main__":
    main()
