export type Kind = 'icmp' | 'dns' | 'tcp' | 'http';
export type Group = 'gateway' | 'mesh' | 'public' | 'dns' | 'device' | 'custom';
export type State = 'up' | 'degraded' | 'down' | 'unknown';

export interface Target {
  id: number;
  name: string;
  kind: Kind;
  host: string;
  port: number | null;
  url: string | null;
  dns_query: string | null;
  dns_rdtype: string | null;
  expect_status: number | null;
  interval_s: number;
  timeout_s: number;
  enabled: boolean;
  group: Group;
  auto: boolean;
  created_at: number;
  updated_at: number;
}

type Nullable<T> = { [K in keyof T]?: T[K] | null };

export type TargetInput = Pick<Target, 'name' | 'kind' | 'interval_s' | 'enabled' | 'group'> &
  Nullable<Pick<Target, 'host' | 'port' | 'url' | 'dns_query' | 'dns_rdtype' | 'expect_status' | 'timeout_s'>>;

export interface Stats {
  count: number;
  lost: number;
  min: number | null;
  p5: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p95: number | null;
  max: number | null;
  avg: number | null;
  jitter: number | null;
  loss_pct: number | null;
}

export interface TargetStatus {
  id: number;
  enabled: boolean;
  state: State;
  consecutive_failures?: number;
  last: { ts_ms: number; ok: boolean; rtt_ms: number | null; error: string | null } | null;
  window_s: number | null;
  stats: Stats | null;
}

export interface StatusResponse {
  now_ms: number;
  targets: TargetStatus[];
}

type Col = (number | null)[];

export interface SeriesResponse {
  target_id: number;
  start: number;
  end: number;
  step: number;
  source: 'raw' | 'rollup';
  series: {
    ts: number[];
    count: number[];
    lost: number[];
    min: Col;
    p5: Col;
    p25: Col;
    median: Col;
    p75: Col;
    p95: Col;
    max: Col;
    avg: Col;
    jitter: Col;
    loss_pct: Col;
  };
  summary: Stats;
}

export interface SystemInfo {
  version: string;
  time: number;
  started_at: number | null;
  default_route: { interface: string; gateway: string } | null;
  database_bytes: number;
  active_targets: number;
  pending_samples: number;
  retention: { raw_days: number; rollup_days: number };
  metrics_enabled: boolean;
}

export interface ProbeResult {
  target_id: number;
  ts_ms: number;
  ok: boolean;
  rtt_ms: number | null;
  error: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { msg?: string; loc?: unknown[] }) => {
        const field = d.loc?.filter((p) => p !== 'body').join('.');
        const msg = (d.msg ?? 'invalid').replace(/^Value error, /, '');
        return field ? `${field}: ${msg}` : msg;
      })
      .join('; ');
  }
  return 'request failed';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      message = describe((await res.json()).detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  system: () => request<SystemInfo>('/api/system'),
  status: () => request<StatusResponse>('/api/status'),
  targets: () => request<Target[]>('/api/targets'),
  target: (id: number) => request<Target>(`/api/targets/${id}`),
  createTarget: (body: TargetInput) =>
    request<Target>('/api/targets', { method: 'POST', body: JSON.stringify(body) }),
  updateTarget: (id: number, body: TargetInput) =>
    request<Target>(`/api/targets/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteTarget: (id: number) => request<void>(`/api/targets/${id}`, { method: 'DELETE' }),
  testTarget: (id: number) => request<ProbeResult>(`/api/targets/${id}/test`, { method: 'POST' }),
  series: (id: number, start: number, end: number, points = 720) =>
    request<SeriesResponse>(`/api/targets/${id}/series?start=${start}&end=${end}&points=${points}`),
  exportUrl: (id: number, start: number, end: number, format: 'csv' | 'json') =>
    `/api/targets/${id}/export?start=${start}&end=${end}&format=${format}`,
};

export function toInput(t: Target): TargetInput {
  return {
    name: t.name,
    kind: t.kind,
    host: t.host,
    port: t.port,
    url: t.url,
    dns_query: t.dns_query,
    dns_rdtype: t.dns_rdtype,
    expect_status: t.expect_status,
    interval_s: t.interval_s,
    timeout_s: t.timeout_s,
    enabled: t.enabled,
    group: t.group,
  };
}
