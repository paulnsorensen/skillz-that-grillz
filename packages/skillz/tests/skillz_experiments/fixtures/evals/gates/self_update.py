import json, pathlib, re
def read(name):
    try:
        return pathlib.Path(name).read_text()
    except OSError:
        return None
out = {p.relative_to('output').as_posix(): p.read_text() for p in pathlib.Path('output').rglob('*') if p.is_file()}
changed = {name: text for name, text in out.items() if read(name) != text}
ok = False
try:
    digest = json.loads(read('digest.json'))
    after = changed['harness-layout.md']
    rejected = after.split('## Rejected', 1)[1]
    kept = read('harness-layout.md').split('## Rejected', 1)[1].strip().splitlines()
    ok = (re.search('^Checked: ' + digest['checked'] + '[.]', after, re.M) is not None
          and all(line in rejected for line in kept)
          and all(r['claim'] in rejected and r['reason'] in rejected for r in digest['rejected'])
          and set(changed) == {'harness-layout.md'})
except Exception:
    pass
print(json.dumps({'score': 1 if ok else 0}))
