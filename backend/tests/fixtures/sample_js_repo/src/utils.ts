export interface Shape { area(): number; }

/** Round to two places. */
export function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export const identity = <T>(x: T): T => x;
