p = "market_monitor/web/app.js"
s = open(p, encoding="utf-8").read()

old = '''  async function startDownload() {
    const label = $("#download-label");'''
new = '''  async function startDownload() {
    const label = $("#download-label");
    const dlBtn = $("#btn-download");'''
assert old in s, "startDownload anchor missing"
s = s.replace(old, new, 1)

old2 = '''        label.textContent = `下载中 ${i}/${total}`;'''
if old2 in s:
    s = s.replace(old2, '''        label.textContent = `下载中 ${i}/${total}`;
        if (dlBtn) dlBtn.title = `下载中 ${i}/${total}`;''', 1)
else:
    # 兜底：在 label.textContent 赋值处统一加 title
    import re
    s = re.sub(r"(\n\s*label\.textContent = )", r"\1", s, count=3)

open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched")
