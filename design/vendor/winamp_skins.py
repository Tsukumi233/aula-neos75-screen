"""Search the Winamp Skin Museum and build contact sheets of skin screenshots."""
import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "_webamp" / "shots"
OUT.mkdir(parents=True, exist_ok=True)
QUERIES = ["hifi", "stereo", "deck", "amplifier", "braun", "silver", "chrome", "aluminium", "brushed", "metal",
           "aqua", "glass", "minimal", "clean", "vfd", "nixie", "amber", "retro", "y2k", "sony", "technics", "tape"]


def gql(query):
    req = urllib.request.Request("https://api.webamp.org/graphql", data=json.dumps({"query": query}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "neos75-design-research"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    seen, rows = set(), []
    for q in QUERIES:
        try:
            res = gql('{ search_skins(query:"%s", first:12){ md5 filename } }' % q)["data"]["search_skins"]
        except Exception as exc:  # noqa: BLE001
            print(q, exc)
            continue
        for s in res:
            if s["md5"] in seen or not s["filename"].lower().endswith((".wsz", ".zip")):
                continue
            seen.add(s["md5"])
            rows.append((q, s["md5"], s["filename"]))
    print(len(rows), "skins")
    for _, md5, _ in rows:
        f = OUT / f"{md5}.png"
        if not f.exists():
            try:
                urllib.request.urlretrieve(f"https://r2.webampskins.org/screenshots/{md5}.png", f)
            except Exception as exc:  # noqa: BLE001
                print(md5, exc)
    (OUT / "index.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 13)
    cols, w, h = 6, 275, 116 * 3 // 1  # museum screenshots are 275x348 (main+eq+playlist)
    per = 36
    for s in range(0, len(rows), per):
        chunk = rows[s:s + per]
        sheet = Image.new("RGB", (cols * (w + 8) + 8, ((len(chunk) + cols - 1) // cols) * (h + 24) + 8), "#111")
        d = ImageDraw.Draw(sheet)
        for k, (q, md5, name) in enumerate(chunk):
            x, y = 8 + (k % cols) * (w + 8), 8 + (k // cols) * (h + 24)
            try:
                im = Image.open(OUT / f"{md5}.png").convert("RGB")
                im.thumbnail((w, h))
                sheet.paste(im, (x, y))
            except Exception:  # noqa: BLE001
                pass
            d.text((x, y + h + 4), f"{s + k:03d} {name[:30]}", fill="#ddd", font=font)
        sheet.save(OUT.parent / f"skins_{s // per:02d}.png")


if __name__ == "__main__":
    main()
