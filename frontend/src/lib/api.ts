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
  since_ms?: number | null;
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
  events?: { id: number; kind: EventKind; start_ms: number; end_ms: number | null }[];
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

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
}

export type UpdateMode = 'off' | 'notify' | 'patch' | 'minor';
export type BumpKind = 'major' | 'minor' | 'patch';

export interface Release {
  version: string;
  tag: string;
  name: string;
  notes: string;
  published_at: string | null;
  url: string | null;
  kind: BumpKind;
}

export interface UpgradeRecord {
  id?: string;
  action?: 'update' | 'rollback';
  version?: string | null;
  state?: string;
  started_at?: string;
  finished_at?: string;
  exit_code?: number;
  rolled_back?: string;
  from_version?: string;
  to_version?: string;
  requested_by?: string;
  reason?: string;
  error?: string;
}

export interface UpdateSummary {
  current: string;
  latest: string | null;
  update_available: boolean;
  latest_kind: BumpKind | null;
  available: Release[];
  auto_target: string | null;
  checked_at: number | null;
  check_error: string | null;
  check_enabled: boolean;
  policy: { mode: UpdateMode; window: string };
  timezone: string;
  in_app_updates: boolean;
  helper: { connected: boolean; last_seen: string | null; agent_version?: string | null };
  progress: {
    state: 'idle' | 'requested' | 'running' | 'succeeded' | 'failed';
    reason?: string;
    request?: { id: string; action: string; version: string | null; requested_at: string };
    status?: UpgradeRecord;
  };
  history: UpgradeRecord[];
  rollback_available: boolean;
}

export type EventKind = 'outage' | 'degraded' | 'nic_link' | 'nic_speed' | 'nic_errors' | 'monitor';

export interface TimelineEvent {
  id: number;
  kind: EventKind;
  severity: 'info' | 'warning' | 'critical';
  target_id: number | null;
  iface: string | null;
  start_ms: number;
  end_ms: number | null;
  duration_ms: number | null;
  open: boolean;
  title: string;
  details: Record<string, unknown>;
}

export interface Hop {
  hop: number;
  host: string | null;
  loss_pct: number;
  sent: number;
  last: number | null;
  avg: number | null;
  best: number | null;
  worst: number | null;
  stdev: number | null;
}

export interface PathTest {
  id: number;
  ts_ms: number;
  target_id: number | null;
  event_id: number | null;
  host: string;
  trigger: 'scheduled' | 'outage' | 'manual';
  status: 'ok' | 'error';
  duration_ms: number | null;
  hop_count: number | null;
  reached: boolean | null;
  first_loss_hop: number | null;
  path_changed: boolean | null;
  hops: Hop[];
  error: string | null;
}

export interface EventDetail {
  event: TimelineEvent;
  target: Target | null;
  window: { start_ms: number; end_ms: number };
  concurrent_failures: TimelineEvent[];
  nic_events: TimelineEvent[];
  other_events: TimelineEvent[];
  path_tests: PathTest[];
}

export interface NicInterface {
  iface: string;
  present: boolean;
  operstate?: string;
  carrier?: number | null;
  speed?: number | null;
  duplex?: string | null;
  carrier_changes?: number | null;
  counters?: Record<string, number>;
}

export interface IperfServer {
  id: number;
  name: string;
  host: string;
  port: number;
  kind: 'lan' | 'wan';
  duration_s: number;
  parallel: number;
  interval_min: number;
  enabled: boolean;
}

export type IperfServerInput = Omit<IperfServer, 'id'>;

export interface ThroughputTest {
  id: number;
  ts_ms: number;
  server_id: number | null;
  host: string;
  kind: 'lan' | 'wan';
  direction: 'download' | 'upload';
  trigger: string;
  status: 'ok' | 'error';
  bits_per_second: number | null;
  retransmits: number | null;
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

/** Fired when the API answers 401, so the app can show the login screen. */
export const UNAUTHORIZED_EVENT = 'netwatch:unauthorized';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      // Required by the server on state-changing requests (CSRF defence).
      'X-Requested-With': 'netwatch',
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
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

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  authStatus: () => request<AuthStatus>('/api/auth/status'),
  setup: (username: string, password: string) =>
    request<{ username: string }>('/api/auth/setup', post({ username, password })),
  login: (username: string, password: string) =>
    request<{ username: string }>('/api/auth/login', post({ username, password })),
  logout: () => request<void>('/api/auth/logout', post()),
  changePassword: (current_password: string, new_password: string) =>
    request<{ username: string }>('/api/auth/password', post({ current_password, new_password })),
  updates: () => request<UpdateSummary>('/api/updates'),
  checkUpdates: () => request<UpdateSummary>('/api/updates/check', post()),
  applyUpdate: (version: string) => request<unknown>('/api/updates/apply', post({ version })),
  rollback: () => request<unknown>('/api/updates/rollback', post()),
  setUpdatePolicy: (mode: UpdateMode, window: string) =>
    request<{ mode: UpdateMode; window: string }>('/api/updates/policy', {
      method: 'PUT',
      body: JSON.stringify({ mode, window }),
    }),
  events: (params: { start?: number; end?: number; kind?: EventKind[]; target_id?: number; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.start) q.set('start', String(params.start));
    if (params.end) q.set('end', String(params.end));
    if (params.target_id) q.set('target_id', String(params.target_id));
    if (params.limit) q.set('limit', String(params.limit));
    for (const k of params.kind ?? []) q.append('kind', k);
    return request<TimelineEvent[]>(`/api/events?${q.toString()}`);
  },
  activeEvents: () => request<TimelineEvent[]>('/api/events/active'),
  event: (id: number) => request<EventDetail>(`/api/events/${id}`),
  nic: () => request<{ poll_s: number; interfaces: NicInterface[] }>('/api/nic'),
  pathTests: (targetId?: number) =>
    request<PathTest[]>(`/api/path-tests${targetId ? `?target_id=${targetId}` : ''}`),
  runPathTest: (body: { target_id?: number; host?: string }) => request<PathTest>('/api/path-tests', post(body)),
  iperfServers: () => request<IperfServer[]>('/api/iperf/servers'),
  createIperfServer: (body: IperfServerInput) => request<IperfServer>('/api/iperf/servers', post(body)),
  updateIperfServer: (id: number, body: IperfServerInput) =>
    request<IperfServer>(`/api/iperf/servers/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteIperfServer: (id: number) => request<void>(`/api/iperf/servers/${id}`, { method: 'DELETE' }),
  runIperf: (id: number) => request<ThroughputTest[]>(`/api/iperf/servers/${id}/run`, post()),
  iperfTests: () => request<ThroughputTest[]>('/api/iperf/tests'),
  upgradeLog: async () => {
    const res = await fetch('/api/updates/log', { credentials: 'same-origin' });
    if (!res.ok) throw new ApiError(res.status, `${res.status}`);
    return res.text();
  },
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
