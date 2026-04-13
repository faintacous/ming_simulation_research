import pathlib, base64
b64path = pathlib.Path(r"G:/AIDev/Ming_Simulation_Research/bio_content.b64")
b64 = b64path.read_text(encoding="ascii")
content = base64.b64decode(b64).decode("utf-8")
t = pathlib.Path(r"G:/AIDev/Ming_Simulation_Research") / "人物名录" / "外国人物" / "小西行长.md"
t.write_text(content, encoding="utf-8")
print("Written:", len(content), "chars,", t.stat().st_size, "bytes,", content.count(chr(10))+1, "lines")
