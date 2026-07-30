# canvas-fonts: not in this repo, they live in Drive

The upstream `ui-styling` skill ships ~81 TTF files in `canvas-fonts/` (5.5 MB), used only for
canvas/image rendering, not for web or app UI work.

They are **not vendored here**, because this repo is cloned to `/tmp/pbs` on every session boot and
5.5 MB would be paid on every start. Removing them took the vendored set from 8.2 MB to 2.8 MB.

## Where they are

Google Drive: **My Drive / General / UI-UX-Pro-Max assets / canvas-fonts.zip** (2.5 MB)
Drive file id `15HCSb8wKj29PyaGSs0J-gBUg0dEquyPZ`

## If you ever need them

```
VAULT=/tmp/pbs python3 /tmp/pbs/drive-api.py get 15HCSb8wKj29PyaGSs0J-gBUg0dEquyPZ /tmp/canvas-fonts.zip
unzip -q /tmp/canvas-fonts.zip -d /tmp/pbs/skills/ui-styling/
```

`references/canvas-design-system.md` still references them and is left intact.
Full resource note: [[ui-ux-pro-max-design-intelligence]].
