export const ACCELERATOR_IDS = [
  'CPU',
  'T4',
  'G4',
  'L4',
  'A100',
  'H100',
  'V5E1',
  'V6E1',
] as const;

export type AcceleratorId = (typeof ACCELERATOR_IDS)[number];
export type MachineShapeType = 'standard' | 'hm';

export const HIGH_MEM_ONLY_ACCELERATORS = new Set<string>(['L4', 'V5E1', 'V6E1']);

export function normalizeAcceleratorId(accelerator?: string | null): string {
  const cleaned = (accelerator || '').trim().toUpperCase();
  if (!cleaned || cleaned === 'NONE') {
    return 'CPU';
  }
  return cleaned;
}

export function isHighMemOnlyAccelerator(accelerator?: string | null): boolean {
  return HIGH_MEM_ONLY_ACCELERATORS.has(normalizeAcceleratorId(accelerator));
}

export function resolveEffectiveShape(
  accelerator?: string | null,
  preferredShape: string = 'standard'
): MachineShapeType {
  if (isHighMemOnlyAccelerator(accelerator)) {
    return 'hm';
  }
  return preferredShape.trim().toLowerCase() === 'hm' ? 'hm' : 'standard';
}
