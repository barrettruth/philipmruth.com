import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const recordsPath = path.join(repoRoot, "data", "vinyl", "records.json");
const generatedPath = path.join(repoRoot, "src", "data", "vinyl.generated.json");
const displayImageSize = {
  width: 360,
  height: 360,
};

const createDisplayImage = (recordId, role) => ({
  src: `/vinyl/${recordId}/display/${role}.webp`,
  ...displayImageSize,
});

const source = JSON.parse(await readFile(recordsPath, "utf8"));

if (source.schemaVersion !== 1 || !Array.isArray(source.records)) {
  throw new Error("Unexpected vinyl records manifest shape");
}

const generated = {
  schemaVersion: 1,
  records: source.records.map((record) => ({
    id: record.id,
    slug: record.slug,
    artist: record.artist,
    title: record.title,
    year: record.year,
    metadata: record.metadata ?? {},
    images: {
      display: {
        front: record.display?.front ? createDisplayImage(record.id, "front") : null,
        back: record.display?.back ? createDisplayImage(record.id, "back") : null,
      },
      actual: [],
    },
  })),
};

await mkdir(path.dirname(generatedPath), { recursive: true });
await writeFile(generatedPath, `${JSON.stringify(generated, null, 2)}\n`);
