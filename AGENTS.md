# AGENTS.md

## Vinyl Environment

Use the project flake for vinyl work:

```bash
nix develop
```

The dev shell now includes the tools expected by the vinyl pipeline:

- `python` with `pillow`, `pillow-heif`, `typer`, and `rich`
- `uv`
- `exiftool`
- `libheif`
- `imagemagick` (`identify`, `magick`, etc.)
- `vips`
- `libwebp` (`cwebp`)

Use these tools instead of ad hoc global installs. Image inspection and preprocessing should happen inside the curated shell.

## Canonical iPhone Transfer Workflow

When a normal local Wi-Fi network is available, use LocalSend as the default iPhone-to-PC transfer path.

1. On NixOS, install LocalSend with `nix profile install nixpkgs#localsend`, or run it once with `nix run nixpkgs#localsend`.
2. Launch LocalSend on Linux. If needed from a terminal, use `localsend_app`.
3. In LocalSend settings on Linux, set the receive folder to `~/Downloads/phil-vinyl`.
4. Make sure the iPhone and laptop are on the same non-guest Wi-Fi network. Turn off VPN if device discovery fails.
5. If LocalSend cannot receive, allow TCP and UDP port `53317` through the Linux firewall.
6. Install LocalSend on the iPhone and grant Local Network and photo access.
7. In the iPhone Photos app, select the batch of photos, tap Share, tap `Options`, set format to `Current`, then share to LocalSend.
8. Choose the Linux device in LocalSend and complete the transfer.
9. Verify file count and spot-check a few files on Linux before deleting anything from the phone.
10. Keep the transferred originals untouched. Do not rename, convert, dedupe, or crop during transfer.

## Vinyl Data Layout

The vinyl pipeline is rooted in `data/vinyl/`.

### Local-only raw intake

- `data/vinyl/raw/` — copied raw intake from LocalSend, gitignored

Raw intake rules:

- LocalSend lands in `~/Downloads/phil-vinyl`
- raw files are then copied into `data/vinyl/raw/`
- raw files remain immutable after copy
- raw files keep their original iPhone names and formats
- expected initial input format is original `.HEIC`

### Committed source of truth

- `data/vinyl/records.json` — committed canonical record manifest

This is the durable collection source of truth. It is not the same thing as the site-facing data export.

### Generated frontend export

- `src/data/vinyl.generated.json` — generated, uncommitted, site-facing export

Rules:

- this file is disposable
- it is derived from `data/vinyl/records.json`
- it should not be hand-edited
- it is gitignored
- the frontend reads this file, not `data/vinyl/records.json`

### Committed public assets

- `public/vinyl/<record-id>/display/front.webp`
- `public/vinyl/<record-id>/display/back.webp`
- `public/vinyl/<record-id>/actual/front.webp`
- `public/vinyl/<record-id>/actual/back.webp`
- `public/vinyl/<record-id>/actual/spine.webp`

These are the deployable site assets and are committed to the repo.

## Identity and Naming Rules

### Stable record IDs

Each record uses a stable, opaque, zero-padded sequential ID:

- `vinyl-0001`
- `vinyl-0002`
- `vinyl-0003`

Do not derive public storage paths from mutable metadata.

### Slugs

Each record also has a human-readable slug derived from the current `artist` + `title`, for example:

- `alice-coltrane-journey-in-satchidananda`

The slug may change if metadata changes. The stable ID must not.

### Raw batch naming

Recommended raw intake batch names:

- `2026-05-24-localsend-01`
- `2026-05-24-localsend-02`

## Canonical Record Model

`data/vinyl/records.json` is record-first, not image-first.

It tracks:

- stable record identity
- human-readable metadata
- raw source photo references
- curated display image provenance
- open-ended metadata for future use

It does **not** redundantly store public asset paths that can be derived from the stable ID.

### `records.json` shape

```json
{
  "schemaVersion": 1,
  "records": [
    {
      "id": "vinyl-0001",
      "slug": "alice-coltrane-journey-in-satchidananda",
      "artist": "Alice Coltrane",
      "title": "Journey in Satchidananda",
      "year": 1971,
      "metadata": {},
      "photos": {
        "front": { "raw": "raw/2026-05-24-localsend-01/IMG_0001.HEIC" },
        "back": { "raw": "raw/2026-05-24-localsend-01/IMG_0002.HEIC" },
        "spine": { "raw": "raw/2026-05-24-localsend-01/IMG_0003.HEIC" },
        "label": null,
        "runout": null
      },
      "display": {
        "front": {
          "source": {
            "kind": "external",
            "url": "https://example.invalid/front.jpg"
          }
        },
        "back": null
      }
    }
  ]
}
```

### Photo roles tracked in the canonical source

Track all meaningful source-photo roles now:

- `front`
- `back`
- `spine`
- `label`
- `runout`

This is true even if the site initially renders much less.

### Display provenance

Display assets may come from different sources over time. Record that in `data/vinyl/records.json`.

Supported provenance kinds should be simple and explicit, for example:

- `external`
- `actual-photo`
- `manual-upload`
- `legacy-placeholder`

If the source is external, store an original URL when known.

## Frontend Export Model

The frontend should not read raw file refs, review notes, or provenance internals directly. It should read the generated export.

### `src/data/vinyl.generated.json` shape

```json
{
  "schemaVersion": 1,
  "records": [
    {
      "id": "vinyl-0001",
      "slug": "alice-coltrane-journey-in-satchidananda",
      "artist": "Alice Coltrane",
      "title": "Journey in Satchidananda",
      "year": 1971,
      "metadata": {},
      "images": {
        "display": {
          "front": {
            "src": "/vinyl/vinyl-0001/display/front.webp",
            "width": 360,
            "height": 360
          },
          "back": null
        },
        "actual": [
          {
            "role": "front",
            "src": "/vinyl/vinyl-0001/actual/front.webp",
            "width": 900,
            "height": 900
          },
          {
            "role": "back",
            "src": "/vinyl/vinyl-0001/actual/back.webp",
            "width": 900,
            "height": 900
          },
          {
            "role": "spine",
            "src": "/vinyl/vinyl-0001/actual/spine.webp",
            "width": 1200,
            "height": 900
          }
        ]
      }
    }
  ]
}
```

Frontend export rules:

- top-level key is `records`, not `albums`
- include `schemaVersion`
- keep `artist`, `title`, and `year` top-level
- `metadata` is open-ended
- `images.display` always has fixed `front` and `back` keys
- `images.display.front` and `images.display.back` are either asset objects or `null`
- `images.actual` is always an ordered gallery array, possibly empty
- each `images.actual` item includes `role`, `src`, `width`, and `height`
- raw source paths never appear in the frontend export
- display/actual distinction must be preserved in the export

## Asset Rules

### Display assets

Display assets are the curated page-rendered images.

Rules:

- format: `webp`
- location: `public/vinyl/<record-id>/display/`
- fixed filenames: `front.webp`, `back.webp`
- fixed size for current grid rendering: `360x360`
- these may come from external art, manual crops, or temporary placeholders

### Actual assets

Actual assets are processed web images derived from the real copy owned.

Rules:

- format: `webp`
- location: `public/vinyl/<record-id>/actual/`
- fixed role filenames: `front.webp`, `back.webp`, `spine.webp`, etc.
- these are curated processed web assets, not raw HEICs
- these preserve role meaning and gallery order comes from the JSON export, not filenames
- exact click-through behavior is not part of the initial rendering scope

## Fallback Rule

For initial listing/grid rendering:

- prefer `images.display.front`
- if `images.display.front` is `null`, the frontend may fall back to the first `images.actual` item whose `role` is `front`
- the generator must not collapse that distinction; the fallback is a frontend concern

## Legacy State in This Repo

The current repo still contains a legacy vinyl setup:

- `src/data/vinyl.json`
- `public/vinyl/*.webp`
- `src/pages/vinyl/index.astro` expecting the old flat `albums` contract

Those root-level WebPs are temporary placeholder art only. They are not a valid long-term contract and should be migrated into the nested `public/vinyl/<record-id>/display/front.webp` structure.

All current placeholder WebPs are already `360x360`, so they can be moved into the new nested display layout without needing resize changes first.

## Current Frontend Scope

The current implementation scope is **initial rendering only**.

In scope:

- loading the generated `records` export
- rendering the vinyl listing/grid
- using `images.display.front` with the defined frontend fallback rule
- migrating placeholder assets into the new nested public layout
- updating the page/data contract away from the old fake `albums` shape

Out of scope for now:

- click-through detail page behavior
- lightbox/gallery interaction
- on-click rendering of actual front/back/spine views
- identification logic
- Discogs upload/sync
- raw intake processing automation beyond the documented storage contract
