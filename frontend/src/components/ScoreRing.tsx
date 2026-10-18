/** The 0-100 health score as a ring, coloured by band. */
export function scoreColour(score: number | null | undefined): string {
  if (score == null) return '#64748b';
  if (score >= 90) return '#34d399';
  if (score >= 75) return '#a3e635';
  if (score >= 50) return '#fbbf24';
  return '#f87171';
}

export function ScoreRing({ score, size = 96, label }: { score: number | null | undefined; size?: number; label?: string }) {
  const r = size / 2 - 7;
  const c = 2 * Math.PI * r;
  const v = score == null ? 0 : Math.max(0, Math.min(100, score));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label ?? `Health score ${score ?? 'unknown'} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1e293b" strokeWidth={8} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={scoreColour(score)}
        strokeWidth={8}
        strokeLinecap="round"
        strokeDasharray={`${(c * v) / 100} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fill="#f8fafc" fontSize={size / 3.2} fontWeight={700}>
        {score ?? '—'}
      </text>
    </svg>
  );
}
