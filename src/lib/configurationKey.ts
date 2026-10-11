/** Canonical key of a configuration for caching and debouncing. The backend's
 *  configuration_id stays the authority for mesh compatibility and review gates. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value as object).sort().map((k) => [k, canonical((value as Record<string, unknown>)[k])]));
  }
  // JavaScript JSON drops the sign of zero; the physics does not care either.
  if (typeof value === 'number' && value === 0) return 0;
  return value;
}

const cache = new WeakMap<object, string>();
export function configurationKey(spec: unknown): string {
  if (!spec || typeof spec !== 'object') return '';
  const cached = cache.get(spec as object);
  if (cached !== undefined) return cached;
  const { name, project_id, ...rest } = spec as Record<string, unknown>;
  void name; void project_id;
  const value = JSON.stringify(canonical(rest));
  cache.set(spec as object, value);
  return value;
}
