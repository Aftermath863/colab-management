/**
 * Format SQLite UTC timestamp ('YYYY-MM-DD HH:MM:SS') or ISO string to local 'YYYY-MM-DD HH:mm:ss'.
 */
export function formatSyncTimestamp(raw?: string | null): string {
  if (!raw) return '';
  const trimmed = raw.trim();
  if (!trimmed) return '';

  const isoCandidate = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(trimmed)
    ? `${trimmed.replace(' ', 'T')}Z`
    : trimmed;

  const date = new Date(isoCandidate);
  if (Number.isNaN(date.getTime())) {
    return trimmed;
  }

  const pad = (n: number) => String(n).padStart(2, '0');
  const yyyy = date.getFullYear();
  const mm = pad(date.getMonth() + 1);
  const dd = pad(date.getDate());
  const hh = pad(date.getHours());
  const min = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  return `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
}

/**
 * Return formatted timestamp of the most recently synced account, or null if none synced.
 */
export function getLatestSyncTimestamp(
  accounts: ReadonlyArray<{ last_synced_at?: string | null }>
): string | null {
  let latestRaw: string | null = null;
  let latestMs = -Infinity;

  for (const acc of accounts) {
    const raw = acc.last_synced_at?.trim();
    if (!raw) continue;
    const isoCandidate = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
      ? `${raw.replace(' ', 'T')}Z`
      : raw;
    const ms = Date.parse(isoCandidate);
    if (!Number.isNaN(ms)) {
      if (ms > latestMs) {
        latestMs = ms;
        latestRaw = raw;
      }
    } else if (!latestRaw || raw > latestRaw) {
      latestRaw = raw;
    }
  }

  return latestRaw ? formatSyncTimestamp(latestRaw) : null;
}
