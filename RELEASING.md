# Releasing

## Before the tag

```powershell
python -m unittest discover -s tests -v     # 32 tests
.\client\tests\smoke.ps1                     # 16 checks
python -m py_compile feed/worker.py feed/reader.py
```

All green, and no local state in the tree:

```powershell
git status --porcelain    # no *.config.json, no *cookie*.json, no feed/state/
```

There is no version constant in the code; the tag is the version.

## The tag

```powershell
git tag -a v1.0.0 -m "quartermaster v1.0.0"
git push origin main --tags
```

## The release notes

Say what changed, and link the diff:

- Engine and client changes, in plain language.
- New or changed example feeds. Those are docs changes; say so.
- The full diff: `/compare/v0.9.0...v1.0.0`.

Attach no build artifacts. The repository is the release; GitHub's automatic
"Source code (zip)" is enough.

## After the tag

- Read the rendered README on the tag, not on `main`.
- Keep the repository description mechanical: "Keeps World of Warcraft addons in
  sync across machines from a feed you control." No brand names in the
  description or topics unless you want the findability.
- If someone asks for an example feed to change, delete or edit that one JSON
  file, note it in the release notes, and tag again. The engine does not
  change.
