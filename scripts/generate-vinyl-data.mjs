import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const recordsPath = path.join(repoRoot, "data", "vinyl", "records.json");
const generatedPath = path.join(repoRoot, "src", "data", "vinyl.generated.json");
const publicVinylPath = path.join(repoRoot, "public", "vinyl");
const actualImageRoles = ["front", "back", "spine", "label", "runout"];
const vinylIdPattern = /^vinyl-\d{4}$/;
const vinylSlugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const fileExists = async (filePath) => {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
};

const readUInt24LE = (buffer, offset) =>
  buffer[offset] | (buffer[offset + 1] << 8) | (buffer[offset + 2] << 16);

const readWebpDimensions = async (filePath) => {
  const buffer = await readFile(filePath);
  const riff = buffer.toString("ascii", 0, 4);
  const webp = buffer.toString("ascii", 8, 12);
  const chunkType = buffer.toString("ascii", 12, 16);

  if (riff !== "RIFF" || webp !== "WEBP") {
    throw new Error(`Unsupported image format for ${filePath}`);
  }

  if (chunkType === "VP8X") {
    return {
      width: readUInt24LE(buffer, 24) + 1,
      height: readUInt24LE(buffer, 27) + 1,
    };
  }

  if (chunkType === "VP8 ") {
    return {
      width: buffer.readUInt16LE(26) & 0x3fff,
      height: buffer.readUInt16LE(28) & 0x3fff,
    };
  }

  if (chunkType === "VP8L") {
    const b0 = buffer[21];
    const b1 = buffer[22];
    const b2 = buffer[23];
    const b3 = buffer[24];

    return {
      width: 1 + (((b1 & 0x3f) << 8) | b0),
      height: 1 + (((b3 & 0x0f) << 10) | (b2 << 2) | ((b1 & 0xc0) >> 6)),
    };
  }

  throw new Error(`Unsupported WebP chunk type ${chunkType} for ${filePath}`);
};

const createImageAsset = async (filePath, publicPath) => ({
  src: publicPath,
  ...(await readWebpDimensions(filePath)),
});

const source = JSON.parse(await readFile(recordsPath, "utf8"));

if (source.schemaVersion !== 1 || !Array.isArray(source.records)) {
  throw new Error("Unexpected vinyl records manifest shape");
}

const seenRecordIds = new Set();

for (const record of source.records) {
  if (!vinylIdPattern.test(record.id)) {
    throw new Error(`Invalid vinyl record id: ${record.id}`);
  }

  if (seenRecordIds.has(record.id)) {
    throw new Error(`Duplicate vinyl record id: ${record.id}`);
  }

  if (!vinylSlugPattern.test(record.slug)) {
    throw new Error(`Invalid vinyl slug: ${record.slug}`);
  }

  seenRecordIds.add(record.id);
}

const generated = {
  schemaVersion: 1,
  records: await Promise.all(
    source.records.map(async (record) => {
      const frontDisplayPath = path.join(publicVinylPath, record.id, "display", "front.webp");
      const backDisplayPath = path.join(publicVinylPath, record.id, "display", "back.webp");
      const hasFrontDisplayAsset = await fileExists(frontDisplayPath);
      const hasBackDisplayAsset = await fileExists(backDisplayPath);

      if (record.display?.front && !hasFrontDisplayAsset) {
        throw new Error(`Missing display front asset for ${record.id}`);
      }

      if (record.display?.back && !hasBackDisplayAsset) {
        throw new Error(`Missing display back asset for ${record.id}`);
      }

      const actualImages = await Promise.all(
        actualImageRoles.map(async (role) => {
          const filePath = path.join(publicVinylPath, record.id, "actual", `${role}.webp`);
          return (await fileExists(filePath))
            ? {
                role,
                ...(await createImageAsset(filePath, `/vinyl/${record.id}/actual/${role}.webp`)),
              }
            : null;
        }),
      );

      return {
        id: record.id,
        slug: record.slug,
        artist: record.artist,
        title: record.title,
        year: record.year,
        metadata: record.metadata ?? {},
        images: {
          display: {
            front: record.display?.front
              ? await createImageAsset(frontDisplayPath, `/vinyl/${record.id}/display/front.webp`)
              : null,
            back: record.display?.back
              ? await createImageAsset(backDisplayPath, `/vinyl/${record.id}/display/back.webp`)
              : null,
          },
          actual: actualImages.filter((image) => image !== null),
        },
      };
    }),
  ),
};

await mkdir(path.dirname(generatedPath), { recursive: true });
await writeFile(generatedPath, `${JSON.stringify(generated, null, 2)}\n`);
