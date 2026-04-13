
import pathlib, base64
data = ""
# Read chunks and decode
target = pathlib.Path(__file__).parent / "小西行长.md"
# Read the b64 file
b64file = pathlib.Path(__file__).parent / "content.b64"
encoded = b64file.read_text(encoding="ascii").replace("
", "")
content = base64.b64decode(encoded).decode("utf-8")
target.write_text(content, encoding="utf-8")
lines = content.count(chr(10)) + 1
print(f"Written: {lines} lines, {len(content)} chars")
