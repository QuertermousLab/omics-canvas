// Local reference resources are optional; uncached assemblies retain their upstream URLs.
export function normalizeReferenceResources(value, config = {}, baseURL) {
  const resources = config?.referenceResources || {};
  const indexedTracks = config?.indexedReferenceTracks || {};
  const localURL = (url) => new URL(String(url).replace(/^\/+/, ""), baseURL).toString();

  function normalize(item) {
    if (Array.isArray(item)) return item.map(normalize);
    if (item && typeof item === "object") {
      const result = Object.fromEntries(Object.entries(item).map(([key, child]) => [key, normalize(child)]));
      if (indexedTracks[item.url]) {
        Object.assign(result, normalize(indexedTracks[item.url]));
      }
      // IGV rewrites legacy remote FASTA references internally. Keep the cached 2bit authoritative.
      if (typeof item.twoBitURL === "string" && (resources[item.twoBitURL] || item.twoBitURL.startsWith("/reference/"))) {
        delete result.fastaURL;
        delete result.indexURL;
      }
      return result;
    }
    if (typeof item === "string") {
      if (resources[item]) return localURL(resources[item]);
      if (item.startsWith("/reference/")) return localURL(item);
    }
    return item;
  }

  return normalize(value);
}
