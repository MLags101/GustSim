/** Display units. Everything stored and sent to the API is SI; conversion happens only here. */
export type Quantity = 'speed' | 'length' | 'area' | 'force' | 'moment' | 'power' | 'pressure' | 'flow' | 'mass_flow' | 'rotation';

interface Unit { label: string; factor: number }
// value_in_unit = si * factor
export const UNITS: Record<Quantity, Record<string, Unit>> = {
  speed: { 'm/s': { label: 'm/s', factor: 1 }, 'km/h': { label: 'km/h', factor: 3.6 }, mph: { label: 'mph', factor: 2.2369362920544 }, kn: { label: 'kn', factor: 1.9438444924406 } },
  length: { m: { label: 'm', factor: 1 }, mm: { label: 'mm', factor: 1000 }, cm: { label: 'cm', factor: 100 }, in: { label: 'in', factor: 39.37007874016 }, ft: { label: 'ft', factor: 3.280839895013 } },
  area: { 'm²': { label: 'm²', factor: 1 }, 'cm²': { label: 'cm²', factor: 1e4 }, 'ft²': { label: 'ft²', factor: 10.76391041671 } },
  force: { N: { label: 'N', factor: 1 }, kgf: { label: 'kgf', factor: 1 / 9.80665 }, lbf: { label: 'lbf', factor: 0.2248089430997 } },
  moment: { 'N·m': { label: 'N·m', factor: 1 }, 'lbf·ft': { label: 'lbf·ft', factor: 0.7375621492783 } },
  power: { W: { label: 'W', factor: 1 }, kW: { label: 'kW', factor: 1e-3 }, hp: { label: 'hp', factor: 1 / 745.69987158227 } },
  pressure: { Pa: { label: 'Pa', factor: 1 }, kPa: { label: 'kPa', factor: 1e-3 }, mbar: { label: 'mbar', factor: 1e-2 }, psi: { label: 'psi', factor: 1.450377377302e-4 } },
  flow: { 'm³/s': { label: 'm³/s', factor: 1 }, 'L/s': { label: 'L/s', factor: 1e3 }, 'L/min': { label: 'L/min', factor: 6e4 }, CFM: { label: 'CFM', factor: 2118.880003 } },
  mass_flow: { 'kg/s': { label: 'kg/s', factor: 1 }, 'kg/h': { label: 'kg/h', factor: 3600 } },
  rotation: { RPM: { label: 'RPM', factor: 1 }, 'rad/s': { label: 'rad/s', factor: Math.PI / 30 } },
};

export type UnitSystem = Record<Quantity, string>;
export const SI: UnitSystem = { speed: 'm/s', length: 'm', area: 'm²', force: 'N', moment: 'N·m', power: 'W', pressure: 'Pa', flow: 'm³/s', mass_flow: 'kg/s', rotation: 'RPM' };
export const PRESETS: Record<string, UnitSystem> = {
  SI,
  Engineering: { ...SI, speed: 'km/h', length: 'mm', power: 'kW' },
  Imperial: { ...SI, speed: 'mph', length: 'in', area: 'ft²', force: 'lbf', moment: 'lbf·ft', power: 'hp', pressure: 'psi', flow: 'CFM' },
};

export function toDisplay(quantity: Quantity, si: number, unit: string) {
  return si * (UNITS[quantity][unit]?.factor ?? 1);
}
export function fromDisplay(quantity: Quantity, value: number, unit: string) {
  return value / (UNITS[quantity][unit]?.factor ?? 1);
}
export function significant(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value === 0) return '0';
  const magnitude = Math.abs(value);
  if (magnitude >= 1e6 || magnitude < 1e-4) return value.toExponential(digits - 1).replace('e+', 'e');
  return Number(value.toPrecision(digits)).toLocaleString(undefined, { maximumFractionDigits: 10 });
}
export function formatQuantity(quantity: Quantity, si: number | null | undefined, unit: string, digits = 4) {
  if (si === null || si === undefined || !Number.isFinite(si)) return '—';
  return `${significant(toDisplay(quantity, si, unit), digits)} ${UNITS[quantity][unit]?.label ?? unit}`;
}
