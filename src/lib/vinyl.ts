export const vinylImageRoles = [
  "front",
  "back",
  "spine",
  "label",
  "runout",
] as const;

export type VinylImageRole = (typeof vinylImageRoles)[number];

export type VinylImage = {
  src: string;
  width: number;
  height: number;
};

export type VinylRecord = {
  id: string;
  slug: string;
  artist: string;
  title: string;
  year: number;
  metadata: Record<string, unknown>;
  images: {
    display: {
      front: VinylImage | null;
      back: VinylImage | null;
    };
    actual: Array<VinylImage & { role: VinylImageRole }>;
  };
};

export type VinylSection = {
  letter: string;
  items: VinylRecord[];
};

export type VinylDetailImage = VinylImage & {
  kind: "actual" | "display";
  role: VinylImageRole | "display-front" | "display-back";
  label: string;
  alt: string;
};

export type VinylMetadataFact = {
  key: string;
  label: string;
  value: string;
};

const detailMetadataLabels = {
  label: "label",
  catalogNumber: "catalog no.",
  format: "format",
  country: "country",
  genre: "genre",
  pressing: "pressing",
} as const;

const detailImageLabels: Record<VinylImageRole, string> = {
  front: "front",
  back: "back",
  spine: "spine",
  label: "label",
  runout: "runout",
};

const capitalize = (value: string) =>
  `${value.charAt(0).toUpperCase()}${value.slice(1)}`;

const isMetadataScalar = (value: unknown): value is string | number =>
  typeof value === "string" || typeof value === "number";

const normalizeMetadataList = (value: unknown) =>
  (Array.isArray(value) ? value : [value])
    .flatMap((item) => (isMetadataScalar(item) ? [String(item).trim()] : []))
    .filter(Boolean);

export const sortRecords = (records: VinylRecord[]) =>
  records
    .slice()
    .sort(
      (left, right) =>
        left.artist.localeCompare(right.artist) ||
        left.title.localeCompare(right.title),
    );

export const buildSections = (records: VinylRecord[]): VinylSection[] =>
  Object.entries(
    records.reduce<Record<string, VinylRecord[]>>((groups, record) => {
      const letter = record.artist.charAt(0).toUpperCase();
      groups[letter] ??= [];
      groups[letter].push(record);
      return groups;
    }, {}),
  )
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([letter, items]) => ({ letter, items }));

export const getDetailHref = (record: VinylRecord) => `/vinyl/${record.id}/`;

export const getListingCover = (record: VinylRecord) =>
  record.images.display.front ??
  record.images.actual.find((image) => image.role === "front") ??
  null;

const getDisplayImages = (record: VinylRecord): VinylDetailImage[] =>
  [
    record.images.display.front
      ? {
          ...record.images.display.front,
          kind: "display" as const,
          role: "display-front" as const,
          label: "front",
          alt: `Front art for ${record.title} by ${record.artist}`,
        }
      : null,
    record.images.display.back
      ? {
          ...record.images.display.back,
          kind: "display" as const,
          role: "display-back" as const,
          label: "back",
          alt: `Back art for ${record.title} by ${record.artist}`,
        }
      : null,
  ].filter((image): image is VinylDetailImage => image !== null);

const getActualImages = (record: VinylRecord): VinylDetailImage[] =>
  vinylImageRoles.flatMap((role) =>
    record.images.actual
      .filter((image) => image.role === role)
      .map((image) => ({
        ...image,
        kind: "actual" as const,
        role,
        label: detailImageLabels[role],
        alt: `${capitalize(detailImageLabels[role])} photograph of ${record.title} by ${record.artist}`,
      })),
  );

export const getDetailImages = (record: VinylRecord) => {
  const actualImages = getActualImages(record);
  return actualImages.length > 0 ? actualImages : getDisplayImages(record);
};

export const getMetadataFacts = (record: VinylRecord): VinylMetadataFact[] =>
  Object.entries(detailMetadataLabels).flatMap(([key, label]) => {
    const values = normalizeMetadataList(record.metadata[key]);
    return values.length > 0 ? [{ key, label, value: values.join(", ") }] : [];
  });

export const getMetadataNotes = (record: VinylRecord) =>
  normalizeMetadataList(record.metadata.notes);

export const getDetailStatus = (record: VinylRecord) => {
  const detailImages = getDetailImages(record);
  const hasActualImages = record.images.actual.length > 0;
  const metadataIsSparse =
    getMetadataFacts(record).length <= 1 &&
    getMetadataNotes(record).length === 0;

  if (!hasActualImages && detailImages.length > 0) {
    return metadataIsSparse
      ? "details pending — showing display art"
      : "showing display art";
  }

  if (detailImages.length === 0) {
    return metadataIsSparse ? "details pending" : "images pending";
  }

  return null;
};
