#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from datetime import UTC, datetime
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pillow_heif
import typer
from PIL import Image, ImageOps
from rich.console import Console
from rich.prompt import Confirm, Prompt

pillow_heif.register_heif_opener()

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()
REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = REPO_ROOT / "data" / "vinyl" / "raw"
STAGING_ROOT = REPO_ROOT / "data" / "vinyl" / "staging"
RECORDS_PATH = REPO_ROOT / "data" / "vinyl" / "records.json"
PUBLIC_VINYL_ROOT = REPO_ROOT / "public" / "vinyl"
SUPPORTED_IMPORT_EXTENSIONS = {".heic", ".heif"}
IMAGE_EXTENSIONS = SUPPORTED_IMPORT_EXTENSIONS | {".jpg", ".jpeg", ".png", ".webp"}
VINYL_ROLES = ["front", "back", "spine", "label", "runout"]
ROLE_COMMANDS = {"f": "front", "b": "back", "s": "spine", "l": "label", "r": "runout"}
NEW_ROLE_COMMANDS = {"nf": "front", "nb": "back", "ns": "spine", "nl": "label", "nr": "runout"}
DISPLAY_SIZE = 360
ACTUAL_MAX_EDGE = 1600
ACTUAL_WEBP_QUALITY = 88


class PipelineError(Exception):
    pass


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf8")


def to_repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def from_repo_relative(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    slug = []
    previous_hyphen = False
    for char in value.lower():
        if char.isalnum():
            slug.append(char)
            previous_hyphen = False
        elif not previous_hyphen:
            slug.append("-")
            previous_hyphen = True
    result = "".join(slug).strip("-")
    return result or "vinyl"


def normalize_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


def load_exif_metadata(paths: list[Path]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}
    command = [
        "exiftool",
        "-json",
        "-DateTimeOriginal",
        "-CreateDate",
        "-ImageWidth",
        "-ImageHeight",
        *[str(path) for path in paths],
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    return {item["SourceFile"]: item for item in payload}


def parse_exif_datetime(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").isoformat()


def generate_batch_name() -> str:
    date_prefix = datetime.now().strftime("%Y-%m-%d")
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    existing = [path.name for path in RAW_ROOT.iterdir() if path.is_dir() and path.name.startswith(f"{date_prefix}-localsend-")]
    numbers = []
    for name in existing:
        try:
            numbers.append(int(name.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    next_number = max(numbers, default=0) + 1
    return f"{date_prefix}-localsend-{next_number:02d}"


def load_records(records_path: Path = RECORDS_PATH) -> dict[str, Any]:
    if not records_path.exists():
        raise PipelineError(f"Missing records manifest: {to_repo_relative(records_path)}")
    payload = read_json(records_path)
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("records"), list):
        raise PipelineError("Unexpected records manifest shape")
    return payload


def find_next_record_id(records: list[dict[str, Any]]) -> str:
    max_seen = 0
    for record in records:
        record_id = record.get("id", "")
        if isinstance(record_id, str) and record_id.startswith("vinyl-"):
            try:
                max_seen = max(max_seen, int(record_id.split("-")[-1]))
            except ValueError:
                continue
    return f"vinyl-{max_seen + 1:04d}"


def list_staging_files() -> list[Path]:
    if not STAGING_ROOT.exists():
        return []
    return sorted(path for path in STAGING_ROOT.glob("*.json") if path.is_file())


def collect_existing_import_hashes() -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for staging_path in list_staging_files():
        try:
            payload = read_json(staging_path)
        except Exception:
            continue
        for image in payload.get("images", []):
            sha = image.get("sourceSha256") or image.get("rawSha256")
            if isinstance(sha, str):
                seen.setdefault(sha, []).append(to_repo_relative(staging_path))
    try:
        records = load_records()
    except Exception:
        return seen
    for record in records.get("records", []):
        photos = record.get("photos") or {}
        for role in VINYL_ROLES:
            photo = photos.get(role)
            if isinstance(photo, dict):
                sha = photo.get("rawSha256")
                if isinstance(sha, str):
                    seen.setdefault(sha, []).append(to_repo_relative(RECORDS_PATH))
    return seen


def build_import_images(source_dir: Path, batch_dir: Path) -> list[dict[str, Any]]:
    source_files = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS
    )
    if not source_files:
        raise PipelineError(f"No HEIC/HEIF files found in {source_dir}")

    metadata = load_exif_metadata(source_files)
    existing_hashes = collect_existing_import_hashes()
    images = []

    for source_path in source_files:
        item = metadata.get(str(source_path), {})
        source_sha = sha256_file(source_path)
        if source_sha in existing_hashes:
            locations = ", ".join(sorted(set(existing_hashes[source_sha])))
            raise PipelineError(
                f"Refusing import because {source_path.name} matches an already-imported file ({locations})."
            )

        raw_path = batch_dir / source_path.name
        shutil.copy2(source_path, raw_path)
        raw_sha = sha256_file(raw_path)
        if raw_sha != source_sha:
            raise PipelineError(f"Hash mismatch after copy for {source_path.name}")

        images.append(
            {
                "filename": source_path.name,
                "sourcePath": str(source_path.resolve()),
                "rawPath": to_repo_relative(raw_path),
                "sourceSha256": source_sha,
                "rawSha256": raw_sha,
                "size": source_path.stat().st_size,
                "capturedAt": parse_exif_datetime(item.get("DateTimeOriginal") or item.get("CreateDate")),
                "width": item.get("ImageWidth"),
                "height": item.get("ImageHeight"),
                "status": "pending",
                "recordDraftId": None,
                "role": None,
            }
        )

    images.sort(key=lambda image: (image["capturedAt"] or "", image["filename"]))
    return images


def create_record_draft(data: dict[str, Any]) -> dict[str, Any]:
    existing_numbers = []
    for draft in data.get("recordDrafts", []):
        draft_id = draft.get("id", "")
        if isinstance(draft_id, str) and draft_id.startswith("draft-"):
            try:
                existing_numbers.append(int(draft_id.split("-")[-1]))
            except ValueError:
                continue
    next_number = max(existing_numbers, default=0) + 1
    draft = {
        "id": f"draft-{next_number:03d}",
        "note": "",
        "identified": {
            "status": "pending",
            "artist": None,
            "title": None,
            "year": None,
            "slug": None,
            "metadata": {},
        },
        "promotedRecordId": None,
        "validation": {"errors": [], "warnings": []},
    }
    data.setdefault("recordDrafts", []).append(draft)
    return draft


def assigned_images_for_draft(data: dict[str, Any], draft_id: str) -> list[dict[str, Any]]:
    return [image for image in data.get("images", []) if image.get("recordDraftId") == draft_id]


def cleanup_empty_drafts(data: dict[str, Any]) -> None:
    used_ids = {image.get("recordDraftId") for image in data.get("images", []) if image.get("recordDraftId")}
    kept = []
    for draft in data.get("recordDrafts", []):
        has_content = (
            draft.get("id") in used_ids
            or bool((draft.get("note") or "").strip())
            or draft.get("promotedRecordId")
            or draft.get("identified", {}).get("status") == "confirmed"
        )
        if has_content:
            kept.append(draft)
    data["recordDrafts"] = kept
    existing_ids = {draft["id"] for draft in kept}
    if data.get("currentRecordDraftId") not in existing_ids:
        data["currentRecordDraftId"] = None


def validate_staging(data: dict[str, Any]) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    assigned_counts: dict[str, int] = {}
    for image in data.get("images", []):
        key = image.get("rawSha256")
        if image.get("recordDraftId") and key:
            assigned_counts[key] = assigned_counts.get(key, 0) + 1

    draft_map = {draft["id"]: draft for draft in data.get("recordDrafts", [])}
    for draft in draft_map.values():
        draft_errors: list[str] = []
        draft_warnings: list[str] = []
        items = assigned_images_for_draft(data, draft["id"])
        identified = draft.get("identified", {})
        has_note = bool((draft.get("note") or "").strip())
        confirmed = identified.get("status") == "confirmed"
        artist = identified.get("artist") if isinstance(identified.get("artist"), str) else ""
        title = identified.get("title") if isinstance(identified.get("title"), str) else ""
        role_map: dict[str, list[str]] = {}
        for image in items:
            role = image.get("role")
            if isinstance(role, str):
                role_map.setdefault(role, []).append(image["filename"])
            if assigned_counts.get(image.get("rawSha256"), 0) > 1:
                draft_errors.append(f"{image['filename']} is assigned more than once")

        if not items and (has_note or confirmed or draft.get("promotedRecordId")):
            draft_errors.append("no images assigned")
        if items and "front" not in role_map:
            draft_errors.append("missing front image")
        if confirmed and not artist.strip():
            draft_errors.append("identified artist missing")
        if confirmed and not title.strip():
            draft_errors.append("identified title missing")

        for role, filenames in sorted(role_map.items()):
            if len(filenames) > 1:
                draft_errors.append(f"duplicate {role} images: {', '.join(filenames)}")

        if items and "back" not in role_map:
            draft_warnings.append("missing back image")
        if items and "spine" not in role_map:
            draft_warnings.append("missing spine image")
        if items and not has_note:
            draft_warnings.append("record note missing")

        draft["validation"] = {
            "errors": sorted(set(draft_errors)),
            "warnings": sorted(set(draft_warnings)),
        }
        errors.extend(f"{draft['id']}: {message}" for message in draft["validation"]["errors"])
        warnings.extend(f"{draft['id']}: {message}" for message in draft["validation"]["warnings"])

    return {"errors": sorted(set(errors)), "warnings": sorted(set(warnings))}


def normalize_staging(data: dict[str, Any]) -> dict[str, Any]:
    cleanup_empty_drafts(data)
    validation = validate_staging(data)
    data["validation"] = validation
    data["updatedAt"] = iso_now()
    return data


def save_staging(path: Path, data: dict[str, Any]) -> None:
    write_json(path, normalize_staging(data))


def load_staging(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if data.get("schemaVersion") != 1 or not isinstance(data.get("images"), list):
        raise PipelineError(f"Unexpected staging shape in {to_repo_relative(path)}")
    data.setdefault("recordDrafts", [])
    data.setdefault("currentIndex", 0)
    data.setdefault("currentRecordDraftId", None)
    data.setdefault("validation", {"errors": [], "warnings": []})
    return normalize_staging(data)


def ensure_preview(source_path: Path, preview_root: Path) -> Path:
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_path = preview_root / f"{source_path.stem}.jpg"
    if preview_path.exists() and preview_path.stat().st_mtime >= source_path.stat().st_mtime:
        return preview_path
    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1800, 1800))
        image.save(preview_path, quality=90)
    return preview_path


def generate_web_asset(source_path: Path, output_path: Path, max_edge: int, quality: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_edge, max_edge))
        image.save(output_path, format="WEBP", quality=quality, method=6)


def make_display_asset(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (DISPLAY_SIZE, DISPLAY_SIZE), method=Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="WEBP", quality=90, method=6)


class ReviewSession:
    def __init__(self, staging_path: Path):
        self.staging_path = staging_path
        self.data = load_staging(staging_path)
        self.preview_root = Path(tempfile.gettempdir()) / "vinyl-review-previews" / self.data["batchId"]
        self.lock = threading.Lock()

    def save(self) -> None:
        with self.lock:
            save_staging(self.staging_path, self.data)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.data)

    def current_image(self) -> dict[str, Any] | None:
        index = self.data.get("currentIndex", 0)
        images = self.data.get("images", [])
        if 0 <= index < len(images):
            return images[index]
        return None

    def current_draft(self) -> dict[str, Any] | None:
        draft_id = self.data.get("currentRecordDraftId")
        if not draft_id:
            return None
        return next((draft for draft in self.data.get("recordDrafts", []) if draft["id"] == draft_id), None)

    def current_preview_path(self) -> Path | None:
        image = self.current_image()
        if image is None:
            return None
        return ensure_preview(from_repo_relative(image["rawPath"]), self.preview_root)

    def draft_preview_items(self, draft_id: str | None) -> list[dict[str, Any]]:
        if draft_id is None:
            return []
        items = []
        for index, image in enumerate(self.data.get("images", [])):
            if image.get("recordDraftId") != draft_id:
                continue
            ensure_preview(from_repo_relative(image["rawPath"]), self.preview_root)
            items.append(
                {
                    "filename": image["filename"],
                    "role": image.get("role"),
                    "previewUrl": f"/preview/{index}.jpg",
                }
            )
        return items

    def browser_state(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        current_index = snapshot.get("currentIndex", 0)
        images = snapshot.get("images", [])
        current_image = images[current_index] if 0 <= current_index < len(images) else None
        current_draft_id = snapshot.get("currentRecordDraftId")
        current_draft = next(
            (draft for draft in snapshot.get("recordDrafts", []) if draft["id"] == current_draft_id),
            None,
        )
        return {
            "batchId": snapshot.get("batchId"),
            "currentIndex": current_index,
            "totalImages": len(images),
            "currentImage": {
                **current_image,
                "previewUrl": f"/preview/{current_index}.jpg?ts={snapshot.get('updatedAt', '')}",
            }
            if current_image
            else None,
            "currentDraft": {
                **current_draft,
                "items": self.draft_preview_items(current_draft["id"]),
            }
            if current_draft
            else None,
            "drafts": [
                {
                    "id": draft["id"],
                    "note": draft.get("note", ""),
                    "identifiedStatus": draft.get("identified", {}).get("status"),
                    "promotedRecordId": draft.get("promotedRecordId"),
                    "validation": draft.get("validation", {"errors": [], "warnings": []}),
                    "roles": [item.get("role") for item in self.draft_preview_items(draft["id"])],
                }
                for draft in snapshot.get("recordDrafts", [])
            ],
            "validation": snapshot.get("validation", {"errors": [], "warnings": []}),
        }


VIEWER_HTML = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>vinyl review</title>
    <style>
      :root { color-scheme: dark; }
      body { margin: 0; font-family: sans-serif; background: #111; color: #eee; }
      .page { display: grid; grid-template-columns: minmax(0, 2fr) minmax(20rem, 1fr); min-height: 100vh; }
      .viewer { display: grid; place-items: center; padding: 1rem; background: #000; }
      .viewer img { max-width: 100%; max-height: calc(100vh - 2rem); object-fit: contain; }
      .sidebar { padding: 1rem; display: grid; gap: 1rem; align-content: start; overflow: auto; }
      h1, h2, p, ul { margin: 0; }
      .muted { color: #aaa; }
      .box { border: 1px solid #333; background: #181818; padding: 0.75rem; display: grid; gap: 0.5rem; }
      .thumbs { display: grid; grid-template-columns: repeat(auto-fill, minmax(5rem, 1fr)); gap: 0.5rem; }
      .thumbs img { width: 100%; display: block; aspect-ratio: 1; object-fit: cover; background: #222; }
      .pill { display: inline-block; padding: 0.15rem 0.45rem; border: 1px solid #444; border-radius: 999px; font-size: 0.8rem; }
      .errors { color: #ff8a8a; }
      .warnings { color: #ffd36a; }
      code { font-family: monospace; }
      @media (max-width: 60rem) { .page { grid-template-columns: 1fr; } .viewer img { max-height: 50vh; } }
    </style>
  </head>
  <body>
    <main class=\"page\">
      <section class=\"viewer\">
        <img id=\"preview\" alt=\"Current vinyl preview\" />
      </section>
      <aside class=\"sidebar\">
        <section class=\"box\">
          <h1>vinyl review</h1>
          <p id=\"progress\" class=\"muted\"></p>
          <p id=\"image-meta\"></p>
          <p id=\"current-group\"></p>
        </section>
        <section class=\"box\">
          <h2>current draft</h2>
          <p id=\"draft-note\" class=\"muted\"></p>
          <div id=\"draft-validation\"></div>
          <div id=\"draft-thumbs\" class=\"thumbs\"></div>
        </section>
        <section class=\"box\">
          <h2>all drafts</h2>
          <div id=\"draft-list\"></div>
        </section>
        <section class=\"box\">
          <h2>commands</h2>
          <p><code>nf nb ns nl nr</code> start new draft with role</p>
          <p><code>f b s l r</code> assign to current draft</p>
          <p><code>n</code> new empty draft</p>
          <p><code>t</code> set record note</p>
          <p><code>u</code> undo</p>
          <p><code>x</code> skip</p>
          <p><code>q</code> quit</p>
        </section>
      </aside>
    </main>
    <script>
      const preview = document.getElementById('preview');
      const progress = document.getElementById('progress');
      const imageMeta = document.getElementById('image-meta');
      const currentGroup = document.getElementById('current-group');
      const draftNote = document.getElementById('draft-note');
      const draftValidation = document.getElementById('draft-validation');
      const draftThumbs = document.getElementById('draft-thumbs');
      const draftList = document.getElementById('draft-list');

      const renderMessages = (items, className) => items.map((value) => `<p class="${className}">${value}</p>`).join('');

      const poll = async () => {
        const response = await fetch('/api/state', { cache: 'no-store' });
        const state = await response.json();
        const image = state.currentImage;
        if (image) {
          preview.src = image.previewUrl;
          progress.textContent = `${state.currentIndex + 1}/${state.totalImages} — ${image.filename}`;
          imageMeta.textContent = `${image.width}×${image.height} • ${image.capturedAt ?? 'no capture time'}`;
        } else {
          preview.removeAttribute('src');
          progress.textContent = 'review complete';
          imageMeta.textContent = '';
        }
        currentGroup.textContent = `current draft: ${state.currentDraft?.id ?? 'none'}`;

        if (state.currentDraft) {
          draftNote.textContent = state.currentDraft.note ? `note: ${state.currentDraft.note}` : 'no note';
          draftValidation.innerHTML = [
            renderMessages(state.currentDraft.validation?.errors ?? [], 'errors'),
            renderMessages(state.currentDraft.validation?.warnings ?? [], 'warnings')
          ].join('');
          draftThumbs.innerHTML = (state.currentDraft.items ?? []).map((item) => `
            <div>
              <img src="${item.previewUrl}" alt="" />
              <p class="muted">${item.role ?? 'unassigned'}</p>
            </div>
          `).join('');
        } else {
          draftNote.textContent = 'no active draft';
          draftValidation.innerHTML = '';
          draftThumbs.innerHTML = '';
        }

        draftList.innerHTML = (state.drafts ?? []).map((draft) => `
          <div class="box">
            <p><strong>${draft.id}</strong> ${draft.promotedRecordId ? `→ ${draft.promotedRecordId}` : ''}</p>
            <p class="muted">roles: ${(draft.roles ?? []).filter(Boolean).join(', ') || 'none'}</p>
            ${renderMessages(draft.validation?.errors ?? [], 'errors')}
            ${renderMessages(draft.validation?.warnings ?? [], 'warnings')}
          </div>
        `).join('');
      };

      poll();
      setInterval(poll, 800);
    </script>
  </body>
</html>
"""


def build_review_handler(session: ReviewSession):
    class Handler(BaseHTTPRequestHandler):
        def _send_bytes(self, payload: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_bytes(VIEWER_HTML.encode("utf8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/state":
                payload = json.dumps(session.browser_state()).encode("utf8")
                self._send_bytes(payload, "application/json; charset=utf-8")
                return
            if parsed.path.startswith("/preview/"):
                name = parsed.path.removeprefix("/preview/")
                try:
                    index = int(name.split(".")[0])
                    image = session.snapshot()["images"][index]
                except Exception:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                preview_path = ensure_preview(from_repo_relative(image["rawPath"]), session.preview_root)
                self._send_bytes(preview_path.read_bytes(), "image/jpeg")
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    return Handler


def assign_current_image(data: dict[str, Any], role: str, *, new_group: bool) -> None:
    index = data.get("currentIndex", 0)
    images = data.get("images", [])
    if not (0 <= index < len(images)):
        raise PipelineError("No current image to assign")
    if new_group or data.get("currentRecordDraftId") is None:
        draft = create_record_draft(data)
        data["currentRecordDraftId"] = draft["id"]
    image = images[index]
    image["status"] = "assigned"
    image["recordDraftId"] = data["currentRecordDraftId"]
    image["role"] = role
    data["currentIndex"] = index + 1


def skip_current_image(data: dict[str, Any]) -> None:
    index = data.get("currentIndex", 0)
    images = data.get("images", [])
    if not (0 <= index < len(images)):
        raise PipelineError("No current image to skip")
    image = images[index]
    image["status"] = "skipped"
    image["recordDraftId"] = None
    image["role"] = None
    data["currentIndex"] = index + 1


def get_current_draft(data: dict[str, Any]) -> dict[str, Any] | None:
    draft_id = data.get("currentRecordDraftId")
    if draft_id is None:
        return None
    return next((draft for draft in data.get("recordDrafts", []) if draft["id"] == draft_id), None)


def update_current_note(data: dict[str, Any], note: str) -> None:
    draft = get_current_draft(data)
    if draft is None:
        draft = create_record_draft(data)
        data["currentRecordDraftId"] = draft["id"]
    draft["note"] = note


def select_draft(data: dict[str, Any], draft_id: str) -> dict[str, Any]:
    draft = next((draft for draft in data.get("recordDrafts", []) if draft["id"] == draft_id), None)
    if draft is None:
        raise PipelineError(f"Unknown draft id: {draft_id}")
    return draft


def build_photo_refs(data: dict[str, Any], draft_id: str) -> dict[str, Any]:
    refs: dict[str, Any] = {role: None for role in VINYL_ROLES}
    for image in assigned_images_for_draft(data, draft_id):
        role = image.get("role")
        if role in refs and refs[role] is None:
            refs[role] = {
                "rawPath": image["rawPath"],
                "rawSha256": image["rawSha256"],
                "filename": image["filename"],
            }
    return refs


def merge_note_into_metadata(metadata: dict[str, Any], note: str) -> dict[str, Any]:
    note = note.strip()
    if not note:
        return metadata
    merged = copy.deepcopy(metadata)
    existing = merged.get("notes")
    if existing is None:
        merged["notes"] = note
        return merged
    if isinstance(existing, list):
        if note not in existing:
            merged["notes"] = [*existing, note]
        return merged
    if isinstance(existing, str):
        if existing == note:
            return merged
        merged["notes"] = [existing, note]
        return merged
    merged["notes"] = note
    return merged


def ensure_actual_asset(
    raw_path: str,
    record_id: str,
    role: str,
    existing_photo: dict[str, Any] | None,
    public_vinyl_root: Path,
) -> str:
    source_path = from_repo_relative(raw_path)
    target_path = public_vinyl_root / record_id / "actual" / f"{role}.webp"
    source_sha = sha256_file(source_path)
    if (
        existing_photo
        and existing_photo.get("rawSha256") == source_sha
        and target_path.exists()
    ):
        return to_repo_relative(target_path)
    generate_web_asset(source_path, target_path, ACTUAL_MAX_EDGE, ACTUAL_WEBP_QUALITY)
    return to_repo_relative(target_path)


@app.command(name="import")
def import_batch(
    source_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    batch: str | None = typer.Option(None, help="Override the auto-generated batch name."),
    force: bool = typer.Option(False, help="Allow importing files even if hashes were seen before."),
) -> None:
    batch_id = batch or generate_batch_name()
    batch_dir = RAW_ROOT / batch_id
    staging_path = STAGING_ROOT / f"{batch_id}.json"

    if batch_dir.exists() or staging_path.exists():
        raise typer.Exit(f"Batch already exists: {batch_id}")

    source_files = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS
    )
    if not source_files:
        raise typer.Exit(f"No HEIC/HEIF files found in {source_dir}")

    existing_hashes = collect_existing_import_hashes()
    overlaps = []
    batch_hashes: dict[str, str] = {}
    for source_path in source_files:
        source_sha = sha256_file(source_path)
        duplicate_name = batch_hashes.get(source_sha)
        if duplicate_name:
            raise typer.Exit(f"Refusing import because {source_path.name} duplicates {duplicate_name} within {source_dir}")
        batch_hashes[source_sha] = source_path.name
        if source_sha in existing_hashes:
            overlaps.append((source_path.name, sorted(set(existing_hashes[source_sha]))))
    if overlaps and not force:
        details = "; ".join(f"{name} already seen in {', '.join(locations)}" for name, locations in overlaps)
        raise typer.Exit(f"Refusing import: {details}")
    if overlaps and force:
        details = "; ".join(f"{name} already seen in {', '.join(locations)}" for name, locations in overlaps)
        console.print(f"[yellow]Force-importing duplicate source file(s):[/yellow] {details}")

    batch_dir.mkdir(parents=True, exist_ok=False)
    images = build_import_images(source_dir, batch_dir) if not force else []
    if force:
        metadata = load_exif_metadata(source_files)
        for source_path in source_files:
            raw_path = batch_dir / source_path.name
            shutil.copy2(source_path, raw_path)
            item = metadata.get(str(source_path), {})
            source_sha = sha256_file(source_path)
            raw_sha = sha256_file(raw_path)
            if raw_sha != source_sha:
                raise typer.Exit(f"Hash mismatch after copy for {source_path.name}")
            images.append(
                {
                    "filename": source_path.name,
                    "sourcePath": str(source_path.resolve()),
                    "rawPath": to_repo_relative(raw_path),
                    "sourceSha256": source_sha,
                    "rawSha256": raw_sha,
                    "size": source_path.stat().st_size,
                    "capturedAt": parse_exif_datetime(item.get("DateTimeOriginal") or item.get("CreateDate")),
                    "width": item.get("ImageWidth"),
                    "height": item.get("ImageHeight"),
                    "status": "pending",
                    "recordDraftId": None,
                    "role": None,
                }
            )
        images.sort(key=lambda image: (image["capturedAt"] or "", image["filename"]))

    payload = {
        "schemaVersion": 1,
        "batchId": batch_id,
        "sourceDir": str(source_dir.resolve()),
        "rawDir": to_repo_relative(batch_dir),
        "importedAt": iso_now(),
        "updatedAt": iso_now(),
        "currentIndex": 0,
        "currentRecordDraftId": None,
        "images": images,
        "recordDrafts": [],
        "validation": {"errors": [], "warnings": []},
    }
    save_staging(staging_path, payload)
    console.print(f"[green]Imported[/green] {len(images)} file(s) into {to_repo_relative(batch_dir)}")
    console.print(f"[green]Staging[/green] {to_repo_relative(staging_path)}")


@app.command()
def review(
    staging_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the review surface automatically."),
) -> None:
    session = ReviewSession(staging_path)
    history: list[dict[str, Any]] = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), build_review_handler(session))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"

    console.print(f"[green]Review batch:[/green] {session.data['batchId']}")
    console.print(f"[green]Persistent viewer:[/green] {url}")
    if not no_browser:
        webbrowser.open(url)

    console.print("Commands: nf nb ns nl nr, f b s l r, n, t, u, x, q")
    try:
        while session.data.get("currentIndex", 0) < len(session.data.get("images", [])):
            image = session.current_image()
            console.print()
            console.print(
                f"[bold]{session.data['currentIndex'] + 1}/{len(session.data['images'])}[/bold] "
                f"{image['filename']} ({image['width']}x{image['height']}, {image['capturedAt'] or 'no-capture-time'})"
            )
            console.print(f"Current draft: {session.data.get('currentRecordDraftId') or 'none'}")
            command = Prompt.ask("command").strip().lower()

            if command in {"?", "h", "help"}:
                console.print("nf nb ns nl nr, f b s l r, n, t, u, x, q")
                continue
            if command == "q":
                session.save()
                break
            if command == "u":
                if not history:
                    console.print("[yellow]Nothing to undo.[/yellow]")
                    continue
                session.data = history.pop()
                session.save()
                continue

            history.append(copy.deepcopy(session.data))

            if command in NEW_ROLE_COMMANDS:
                assign_current_image(session.data, NEW_ROLE_COMMANDS[command], new_group=True)
            elif command in ROLE_COMMANDS:
                assign_current_image(session.data, ROLE_COMMANDS[command], new_group=False)
            elif command == "n":
                draft = create_record_draft(session.data)
                session.data["currentRecordDraftId"] = draft["id"]
            elif command == "t":
                note = Prompt.ask("record note").strip()
                update_current_note(session.data, note)
            elif command == "x":
                skip_current_image(session.data)
            else:
                console.print("[red]Unknown command.[/red]")
                history.pop()
                continue

            session.save()
            validation = session.data.get("validation", {})
            if validation.get("errors"):
                console.print(f"[red]Errors:[/red] {'; '.join(validation['errors'])}")
            elif validation.get("warnings"):
                console.print(f"[yellow]Warnings:[/yellow] {'; '.join(validation['warnings'])}")
    finally:
        server.shutdown()
        server.server_close()
        session.save()

    console.print(f"[green]Saved[/green] {to_repo_relative(staging_path)}")


@app.command()
def identify(
    staging_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    draft_id: str | None = typer.Argument(None),
    artist: str | None = typer.Option(None),
    title: str | None = typer.Option(None),
    year: int | None = typer.Option(None),
    metadata_json: Path | None = typer.Option(None, exists=True, file_okay=True, dir_okay=False),
) -> None:
    data = load_staging(staging_path)
    drafts = data.get("recordDrafts", [])
    if not drafts:
        raise typer.Exit("No record drafts in staging file")

    if draft_id is None:
        choices = [draft["id"] for draft in drafts]
        draft_id = Prompt.ask("draft id", choices=choices, default=choices[0])

    draft = select_draft(data, draft_id)
    artist = (artist or Prompt.ask("artist", default=draft.get("identified", {}).get("artist") or "")).strip()
    title = (title or Prompt.ask("title", default=draft.get("identified", {}).get("title") or "")).strip()
    if not artist:
        raise typer.Exit("Artist is required")
    if not title:
        raise typer.Exit("Title is required")
    year_value = (
        year
        if year is not None
        else int(Prompt.ask("year", default=str(draft.get("identified", {}).get("year") or datetime.now().year)))
    )
    metadata = draft.get("identified", {}).get("metadata") or {}
    if metadata_json is not None:
        metadata = normalize_metadata(read_json(metadata_json))

    draft["identified"] = {
        "status": "confirmed",
        "artist": artist,
        "title": title,
        "year": year_value,
        "slug": slugify(f"{artist}-{title}"),
        "metadata": metadata,
    }
    save_staging(staging_path, data)
    console.print(f"[green]Identified[/green] {draft_id} as {artist} — {title} ({year_value})")


@app.command()
def promote(
    staging_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    records_path: Path = typer.Option(RECORDS_PATH, help="Override the canonical records.json path."),
    public_root: Path = typer.Option(PUBLIC_VINYL_ROOT, help="Override the public vinyl asset root."),
) -> None:
    data = load_staging(staging_path)
    records_payload = load_records(records_path)
    records = records_payload["records"]
    promoted = 0

    for draft in data.get("recordDrafts", []):
        identified = draft.get("identified", {})
        if draft.get("promotedRecordId"):
            continue
        if identified.get("status") != "confirmed":
            continue
        if draft.get("validation", {}).get("errors"):
            raise typer.Exit(f"Cannot promote {draft['id']} with validation errors")
        artist = identified.get("artist").strip() if isinstance(identified.get("artist"), str) else ""
        title = identified.get("title").strip() if isinstance(identified.get("title"), str) else ""
        year_value = identified.get("year")
        if not artist:
            raise typer.Exit(f"Cannot promote {draft['id']} without an artist")
        if not title:
            raise typer.Exit(f"Cannot promote {draft['id']} without a title")
        if not isinstance(year_value, int):
            raise typer.Exit(f"Cannot promote {draft['id']} without a year")

        record_id = find_next_record_id(records)
        slug = identified.get("slug") or slugify(f"{artist}-{title}")
        photo_refs = build_photo_refs(data, draft["id"])
        if not any(isinstance(ref, dict) for ref in photo_refs.values()):
            raise typer.Exit(f"Cannot promote {draft['id']} without assigned images")
        if photo_refs["front"] is None:
            raise typer.Exit(f"Cannot promote {draft['id']} without a front image")
        existing_record = next((record for record in records if record.get("id") == record_id), None)
        existing_photos = existing_record.get("photos", {}) if existing_record else {}

        for role, ref in photo_refs.items():
            if isinstance(ref, dict):
                ensure_actual_asset(ref["rawPath"], record_id, role, existing_photos.get(role) or ref, public_root)

        metadata = merge_note_into_metadata(
            normalize_metadata(identified.get("metadata")),
            draft.get("note", ""),
        )

        records.append(
            {
                "id": record_id,
                "slug": slug,
                "artist": artist,
                "title": title,
                "year": year_value,
                "metadata": metadata,
                "photos": photo_refs,
                "display": {"front": None, "back": None},
            }
        )
        draft["promotedRecordId"] = record_id
        promoted += 1

    if promoted == 0:
        console.print("[yellow]No confirmed unpromoted drafts found.[/yellow]")
        return

    write_json(records_path, records_payload)
    save_staging(staging_path, data)
    console.print(f"[green]Promoted[/green] {promoted} draft(s) into {to_repo_relative(records_path)}")


if __name__ == "__main__":
    app()
