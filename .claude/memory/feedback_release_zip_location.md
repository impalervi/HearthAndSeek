---
name: Release zip location
description: Release zip archives must go in dist/ inside the repo, never in /tmp or other external directories
type: feedback
---

Release zip archives must always be created in `dist/` inside the repo (`d:\Programming\WoWAddons\HearthAndSeek\dist\`), never in `/tmp`, `/d/tmp`, or any other external directory. The `dist/` folder already contains all prior release zips (v0.1 through v1.4.0).

Command pattern:
```bash
git archive --format=zip --prefix=HearthAndSeek/ -o dist/HearthAndSeek-v{VERSION}.zip v{VERSION} -- Core/ Data/ Libs/ Media/ Modules/ UI/ HearthAndSeek.toc
```
