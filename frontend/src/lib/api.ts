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

export type EventKind =
  | 'outage'
  | 'degraded'
  | 'nic_link'
  | 'nic_speed'
  | 'nic_errors'
  | 'monitor'
  | 'anomaly'
  | 'device'
  | 'security'
  | 'wifi';

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

export type DeviceType =
  | 'router'
  | 'access_point'
  | 'network'
  | 'nas'
  | 'server'
  | 'computer'
  | 'phone'
  | 'tablet'
  | 'tv'
  | 'media_player'
  | 'speaker'
  | 'printer'
  | 'camera'
  | 'game_console'
  | 'iot'
  | 'unknown';

export interface DeviceRow {
  id: number;
  mac: string;
  vendor: string | null;
  randomised: boolean;
  name: string | null;
  hostname: string | null;
  mdns_name: string | null;
  model: string | null;
  type: DeviceType;
  type_auto: DeviceType;
  type_reason: string | null;
  type_override: DeviceType | null;
  merged_into: number | null;
  target_id: number | null;
  first_seen_ms: number;
  last_seen_ms: number;
  last_ip: string | null;
  notes: string | null;
  details: {
    sources?: string[];
    services?: string[];
    ssdp?: string[];
    friendly?: string;
    ptr?: string;
    dhcp_vendor?: string;
    dhcp_server?: boolean;
    deco_mac?: string;
    deco_name?: string;
    connection?: string;
    app_name?: string;
    is_deco?: boolean;
  };
  label: string;
  online: boolean;
  macs: string[];
}

export interface DeviceMember {
  id: number;
  mac: string;
  vendor: string | null;
  randomised: boolean;
  hostname: string | null;
  first_seen_ms: number;
  last_seen_ms: number;
  last_ip: string | null;
  merged_into: number | null;
}

export interface DeviceDetail {
  device: DeviceRow;
  members: DeviceMember[];
  ips: { device_id: number; ip: string; first_seen_ms: number; last_seen_ms: number; source: string }[];
  target: Target | null;
  events: TimelineEvent[];
}

export interface DiscoveryScan {
  trigger: string;
  started_ms: number;
  finished_ms?: number;
  networks?: string[];
  hosts?: number;
  notes?: string[];
  errors?: string[];
  error?: string;
  reverse_dns_names?: number;
}

export interface DiscoveryStatus {
  interface: string | null;
  address: string | null;
  passive: {
    enabled: boolean;
    running: boolean;
    error: string | null;
    frames: number;
    dropped: number;
    packets: Record<string, number>;
    arp_pps: number;
  };
  active: {
    enabled: boolean;
    interval_min: number;
    rate_pps: number;
    networks: string[];
    notes: string[];
    scanning: boolean;
    unavailable: string | null;
    last_scan: DiscoveryScan | null;
  };
  gateway: { ip: string | null; mac: string | null };
  dhcp_servers: { ip: string; mac: string; vendor: string | null; last_seen_ms: number }[];
  dns_check: { ts_ms: number; resolvers: string[]; findings: string[] } | null;
  learning_until_ms: number | null;
  devices: { total: number; online: number };
}

export type DeviceEdit = Partial<Pick<DeviceRow, 'name' | 'type_override' | 'notes'>>;

export interface TopologyNode {
  layer: string;
  parent: string | null;
  label: string;
  state: State;
  inferred: boolean;
  members: { target_id?: number; name: string; host?: string; state: State | 'up' | 'down' }[];
}

export interface Cause {
  id: string;
  title: string;
  layer: string;
  confidence: number;
  evidence: string[];
  next_steps: string[];
}

export interface IncidentTarget {
  target_id: number;
  name: string;
  host: string;
  layer: string;
  kinds?: string[];
  loss_pct?: number;
}

export interface BriefEvent {
  id: number;
  kind: EventKind;
  severity: string;
  start_ms: number;
  end_ms: number | null;
  title: string;
  type: string | null;
}

export interface Incident {
  id: string;
  start_ms: number;
  end_ms: number | null;
  open: boolean;
  duration_ms: number;
  event_ids: number[];
  severity: 'critical' | 'warning';
  failing: IncidentTarget[];
  healthy: IncidentTarget[];
  failing_layers: string[];
  nic_events: BriefEvent[];
  anomalies: BriefEvent[];
  wifi_events?: BriefEvent[];
  path_tests: { id: number; ts_ms: number; host: string; first_loss_hop: number | null; reached: boolean | null }[];
  causes: Cause[];
}

export type MapNodeKind = 'internet' | 'isp' | 'hub' | 'switch' | 'host' | 'deco' | 'device' | 'group';

export interface MapMember {
  target_id: number;
  name: string;
  host: string;
  state?: State;
  median_ms?: number | null;
  loss_pct?: number | null;
}

export interface MapGroupDevice {
  device_id: number;
  label: string;
  ip: string | null;
  type: string;
  connection?: string | null;
}

export interface MapNode {
  id: string;
  kind: MapNodeKind;
  label: string;
  state: State;
  inferred: boolean;
  members: MapMember[];
  info: {
    ip?: string | null;
    mac?: string;
    model?: string | null;
    role?: 'main' | 'satellite';
    backhaul?: string;
    firmware?: string | null;
    device_id?: number | null;
    type?: string;
    vendor?: string | null;
    connection?: string | null;
    online?: boolean;
    count?: number;
    devices?: MapGroupDevice[];
    interfaces?: { name: string; speed: number | null; state: string | null }[];
  };
}

export interface MapEdge {
  id: string;
  source: string;
  target: string;
  medium: 'wired' | 'wireless' | 'logical';
  median_ms: number | null;
  loss_pct: number | null;
  label: string | null;
}

export interface MapHighlight {
  incident_id: string;
  start_ms: number;
  cause: Cause | null;
  cause_nodes: string[];
  failing_nodes: string[];
}

export interface NetworkMapResponse {
  generated_ms: number;
  at_ms: number | null;
  deco_api: boolean;
  nodes: MapNode[];
  edges: MapEdge[];
  highlight?: MapHighlight;
}

export type MapPositions = Record<string, { x: number; y: number }>;

export interface DecoUnit {
  mac: string;
  name: string;
  ip: string | null;
  model: string | null;
  firmware: string | null;
  role: 'main' | 'satellite';
  online: boolean;
  backhaul: string;
}

export interface DecoStatus {
  host: string;
  enabled: boolean;
  password_set: boolean;
  last_poll_ms: number | null;
  last_ok_ms: number | null;
  error: string | null;
  paused: string | null;
  decos: DecoUnit[];
  clients: number;
  suggestions: { ip: string; label: string }[];
}

export interface DecoTestResult {
  ok: boolean;
  error?: string;
  decos?: { name: string; role: string; model: string | null; online: boolean; backhaul: string }[];
  clients?: number;
}

export interface Pattern {
  kind: 'interval' | 'time_of_day';
  title: string;
  hint: string;
}

export interface DiagnosisResponse {
  now_ms: number;
  topology: TopologyNode[];
  patterns: Pattern[];
  incidents: Incident[];
}

export interface ReportIncident extends Incident {
  start: string;
  end: string;
  duration: string;
  cause: string;
  confidence: number;
  path_hops: { host: string; ts: string; first_loss_hop: number | null; hops: { hop: number; host: string | null; loss_pct: number; avg: number | null }[] }[];
}

export interface Report {
  generated_at: string;
  version: string;
  period: { start: string; end: string; start_ms: number; end_ms: number };
  scope: 'all' | 'isp';
  gateway: string | null;
  summary: {
    incidents: number;
    isp_incidents: number;
    isp_downtime: string;
    isp_availability_pct: number;
    longest: string;
  };
  patterns: Pattern[];
  targets: { name: string; host: string; layer_label: string; probes: number; loss_pct: number | null; median_ms: number | null; p95_ms: number | null }[];
  speeds: { kind: string; direction: string; tests: number; avg_bps: number; min_bps: number; max_bps: number }[];
  ruled_out: string[];
  incidents: ReportIncident[];
  markdown: string;
}

export interface SettingField {
  name: string;
  group: string;
  group_label: string;
  label: string;
  help: string;
  unit: string;
  live: boolean;
  type: 'int' | 'float' | 'bool' | 'str' | 'choice';
  value: number | boolean | string;
  default: number | boolean | string;
  overridden: boolean;
  min?: number;
  max?: number;
  choices?: string[];
  secret?: boolean;
}

export interface SettingsResponse {
  fields: SettingField[];
  restart_pending: string[];
}

export interface SessionInfo {
  id: string;
  username: string;
  created_ms: number;
  last_seen_ms: number;
  expires_ms: number;
  ip: string;
  user_agent: string;
  current: boolean;
}

export interface BackupFile {
  name: string;
  bytes: number;
  modified: number;
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
  devices: (q?: string) =>
    request<{ now_ms: number; devices: DeviceRow[] }>(`/api/devices${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  device: (id: number) => request<DeviceDetail>(`/api/devices/${id}`),
  editDevice: (id: number, body: DeviceEdit) =>
    request<DeviceDetail>(`/api/devices/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  mergeDevice: (id: number, into: number) => request<DeviceDetail>(`/api/devices/${id}/merge`, post({ into })),
  unmergeDevice: (id: number) => request<DeviceDetail>(`/api/devices/${id}/unmerge`, post()),
  monitorDevice: (id: number) => request<DeviceDetail>(`/api/devices/${id}/monitor`, post()),
  unmonitorDevice: (id: number) => request<DeviceDetail>(`/api/devices/${id}/monitor`, { method: 'DELETE' }),
  forgetDevice: (id: number) => request<void>(`/api/devices/${id}`, { method: 'DELETE' }),
  discovery: () => request<DiscoveryStatus>('/api/discovery'),
  setDiscovery: (active: boolean) =>
    request<DiscoveryStatus>('/api/discovery', { method: 'PUT', body: JSON.stringify({ active }) }),
  scanNow: () => request<DiscoveryScan>('/api/discovery/scan', post()),
  diagnosis: (start?: number) =>
    request<DiagnosisResponse>(`/api/diagnosis${start ? `?start=${start}` : ''}`),
  diagnosisForEvent: (eventId: number) => request<Incident>(`/api/diagnosis/event/${eventId}`),
  report: (start: number, scope: 'all' | 'isp') => request<Report>(`/api/report?start=${start}&scope=${scope}`),
  reportMarkdownUrl: (start: number, scope: 'all' | 'isp') => `/api/report?start=${start}&scope=${scope}&format=md`,
  map: (params: { at?: number; incident?: string; event?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.at) q.set('at', String(params.at));
    if (params.incident) q.set('incident', params.incident);
    if (params.event) q.set('event', String(params.event));
    const qs = q.toString();
    return request<NetworkMapResponse>(`/api/map${qs ? `?${qs}` : ''}`);
  },
  mapLayout: () => request<{ positions: MapPositions }>('/api/map/layout'),
  saveMapLayout: (positions: MapPositions) =>
    request<{ positions: MapPositions }>('/api/map/layout', { method: 'PUT', body: JSON.stringify({ positions }) }),
  resetMapLayout: () => request<void>('/api/map/layout', { method: 'DELETE' }),
  deco: () => request<DecoStatus>('/api/admin/deco'),
  saveDeco: (body: { host: string; enabled: boolean; password?: string; forget_password?: boolean }) =>
    request<DecoStatus>('/api/admin/deco', { method: 'PUT', body: JSON.stringify(body) }),
  testDeco: (host: string, password?: string) => request<DecoTestResult>('/api/admin/deco/test', post({ host, password })),
  adminSettings: () => request<SettingsResponse>('/api/admin/settings'),
  saveSettings: (changes: Record<string, unknown>) =>
    request<SettingsResponse>('/api/admin/settings', { method: 'PUT', body: JSON.stringify({ changes }) }),
  restart: () => request<{ restarting: boolean }>('/api/admin/restart', post()),
  sessions: () => request<SessionInfo[]>('/api/admin/sessions'),
  revokeSession: (id: string) => request<void>(`/api/admin/sessions/${id}`, { method: 'DELETE' }),
  revokeOtherSessions: () => request<{ revoked: number }>('/api/admin/sessions/revoke-others', post()),
  securityLog: () => request<TimelineEvent[]>('/api/admin/security-log'),
  backups: () => request<{ backups: BackupFile[]; restore_pending: boolean; database_bytes: number }>('/api/admin/backups'),
  createBackup: () => request<BackupFile>('/api/admin/backups', post()),
  deleteBackup: (name: string) => request<void>(`/api/admin/backups/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  backupUrl: (name: string) => `/api/admin/backups/${encodeURIComponent(name)}`,
  restoreBackup: (name: string) => request<{ restarting: boolean }>(`/api/admin/backups/${encodeURIComponent(name)}/restore`, post()),
  restoreUpload: (file: Blob) =>
    request<{ restarting: boolean; targets: number; users: number }>('/api/admin/restore', {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': 'application/octet-stream' },
    }),
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
