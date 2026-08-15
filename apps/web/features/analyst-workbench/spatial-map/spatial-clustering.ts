/**
 * Lightweight, deterministic "same-screen" aggregation used by the Leaflet
 * renderer. It intentionally avoids pulling in a full clustering dependency:
 * CaseFile's contract mode never needs grid-indexed server clusters, only a
 * stable grouping so nearby markers stay readable at small zooms.
 */

export interface SpatialClusterPoint {
  x: number;
  y: number;
}

export interface SpatialCluster {
  keys: string[];
  x: number;
  y: number;
}

export interface SpatialClusterOptions {
  /** Pixel distance below which two markers are grouped. */
  radius: number;
  excludedKeys: ReadonlySet<string>;
}

export function computeSpatialClusters(
  points: ReadonlyMap<string, SpatialClusterPoint>,
  options: SpatialClusterOptions,
): SpatialCluster[] {
  const keys = [...points.keys()]
    .filter((key) => !options.excludedKeys.has(key))
    .sort();
  const groups: Array<{ keys: string[]; x: number; y: number }> = [];

  for (const key of keys) {
    const point = points.get(key);
    if (!point) continue;
    const nearest = groups.find(
      (group) =>
        Math.hypot(group.x - point.x, group.y - point.y) <= options.radius,
    );
    if (nearest) {
      nearest.keys.push(key);
      nearest.x =
        (nearest.x * (nearest.keys.length - 1) + point.x) / nearest.keys.length;
      nearest.y =
        (nearest.y * (nearest.keys.length - 1) + point.y) / nearest.keys.length;
    } else {
      groups.push({ keys: [key], x: point.x, y: point.y });
    }
  }

  return groups
    .filter((group) => group.keys.length > 1)
    .map((group) => ({ keys: group.keys, x: group.x, y: group.y }))
    .sort((left, right) => left.keys[0].localeCompare(right.keys[0]));
}
