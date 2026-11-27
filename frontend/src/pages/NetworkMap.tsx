import {
  Background,
  ConnectionMode,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type Incident, type MapEdge, type MapHighlight, type MapNode, type MapPins, type MapPositions, type MapSide, type MapView, type NetworkMapResponse } from '../lib/api';
import { GroupIcon, colourOf } from '../lib/groups';
import { shapeGraph, type ColourBy } from '../lib/mapGroups';
import { TopTalkers } from '../components/TopTalkers';
import { DEVICE_TYPE_LABEL, STATE_STYLE, fmtBps, fmtDurationMs, fmtMs, fmtPct, fmtTime, lossColour } from '../lib/format';
import { edgeHandles } from '../lib/edgeSides';
import { NODE_H, NODE_W, layoutMap } from '../lib/mapLayout';

const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';
const input =
  'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const DAY_MS = 86_400_000;
const REPLAY_SPAN_MS = 7 * DAY_MS;

const ICON: Record<MapNode['kind'], string> = {
  internet: '🌐',
  isp: '🏢',
  hub: '📡',
  switch: '🔀',
  host: '🗄️',
  deco: '📶',
  device: '💻',
  group: '⋯',
  devgroup: '▣',
  remote: '☁️',
};

type MapNodeData = { node: MapNode; mark: 'cause' | 'failing' | null; accent: string | null; faded: boolean };
type FlowNode = Node<MapNodeData, 'net'>;

function rates(t: { down_bps: number; up_bps: number }): string {
  return `↓${fmtBps(t.down_bps)} ↑${fmtBps(t.up_bps)}`;
}

function subline(n: MapNode): string {
  if (n.traffic) return rates(n.traffic);
  if (n.kind === 'group' || n.kind === 'devgroup') return 'tap to list';
  const m = n.members.find((x) => x.median_ms != null);
  const parts = [n.info.ip ?? n.info.host, n.info.backhaul && n.kind === 'deco' ? n.info.backhaul : null, m ? fmtMs(m.median_ms) : null];
  return parts.filter(Boolean).join(' · ') || (n.inferred ? 'inferred' : '');
}

// Every tile has a connection point on each side; each link picks its own (see lib/edgeSides).
const SIDES: [MapSide, Position][] = [['top', Position.Top], ['bottom', Position.Bottom], ['left', Position.Left], ['right', Position.Right]];

function NetNode({ data, selected }: NodeProps<FlowNode>) {
  const { node: n, mark, accent, faded } = data;
  const group = n.info.group;
  const tags = n.info.tags ?? [];
  const st = STATE_STYLE[n.state];
  const ring =
    mark === 'cause' ? 'ring-4 ring-rose-500' : mark === 'failing' ? 'ring-2 ring-amber-400' : selected ? 'ring-2 ring-sky-500' : `ring-1 ${st.ring}`;
  return (
    <div
      className={`relative flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-left shadow transition-opacity ${ring} ${n.inferred ? 'border border-dashed border-slate-600' : ''} ${faded ? 'opacity-25' : ''} ${n.kind === 'devgroup' ? 'bg-slate-800' : ''}`}
      style={{ width: NODE_W, height: NODE_H, ...(accent ? { borderLeft: `4px solid ${accent}` } : {}) }}
      data-testid={`node-${n.id}`}
    >
      {group && (n.kind === 'device' || n.kind === 'devgroup') ? (
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md" style={{ color: colourOf(group.colour).hex, background: `${colourOf(group.colour).hex}22` }} aria-hidden>
          <GroupIcon icon={group.icon} className="h-4 w-4" />
        </span>
      ) : (
        <span className="text-xl" aria-hidden>{ICON[n.kind]}</span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} title={st.label} />
          <span className="truncate text-sm font-medium text-slate-100">{n.label}</span>
        </div>
        <div className="truncate text-[11px] text-slate-400">{subline(n)}</div>
      </div>
      {mark === 'cause' && <span className="absolute -top-2 right-2 rounded bg-rose-600 px-1 text-[10px] text-white">likely cause</span>}
      {tags.length > 0 && (
        <span className="absolute -bottom-2 right-2 flex gap-1">
          {tags.slice(0, 2).map((t) => <span key={t} className="rounded-full bg-slate-700 px-1.5 text-[9px] leading-4 text-slate-200">#{t}</span>)}
          {tags.length > 2 && <span className="rounded-full bg-slate-700 px-1.5 text-[9px] leading-4 text-slate-300">+{tags.length - 2}</span>}
        </span>
      )}
      {SIDES.map(([id, position]) => (
        <Handle key={id} id={id} type="source" position={position} isConnectable={false} className="!h-1 !w-1 !border-0 !bg-slate-600" />
      ))}
    </div>
  );
}

const nodeTypes = { net: NetNode };

function edgeLabel(e: MapEdge): string | undefined {
  const stats = [e.median_ms != null ? fmtMs(e.median_ms) : null, e.loss_pct ? `${fmtPct(e.loss_pct)} loss` : null].filter(Boolean);
  const text = [e.label, ...stats, e.traffic && rates(e.traffic)].filter(Boolean).join(' · ');
  return text || undefined;
}

function toFlow(g: { nodes: MapNode[]; edges: MapEdge[] }, pos: MapPositions, hl: MapHighlight | undefined,
  accent: Map<string, string | null>, faded: Set<string>): { nodes: FlowNode[]; edges: Edge[] } {
  const cause = new Set(hl?.cause_nodes ?? []);
  const failing = new Set(hl?.failing_nodes ?? []);
  const nodes: FlowNode[] = g.nodes.map((n) => ({
    id: n.id,
    type: 'net',
    position: pos[n.id] ?? { x: 0, y: 0 },
    data: { node: n, mark: cause.has(n.id) ? 'cause' : failing.has(n.id) ? 'failing' : null, accent: accent.get(n.id) ?? null, faded: faded.has(n.id) },
  }));
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const edges: Edge[] = g.edges.map((e) => {
    const down = byId.get(e.target)?.state === 'down' || byId.get(e.source)?.state === 'down';
    const colour = down ? '#ef4444' : e.loss_pct != null ? lossColour(e.loss_pct) : '#475569';
    const flow = e.traffic ? e.traffic.down_bps + e.traffic.up_bps : 0;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: edgeLabel(e),
      labelStyle: { fill: '#cbd5e1', fontSize: 11 },
      labelBgStyle: { fill: '#0f172a' },
      // Busy links move and get thicker (log scale: 1 Mb/s → 2 px, 1 Gb/s → 6 px).
      animated: !down && (e.medium === 'wireless' || flow > 1e5),
      style: {
        stroke: colour,
        strokeWidth: flow > 1e5 ? Math.min(6, 2 + Math.log10(flow / 1e6) * (4 / 3)) : e.medium === 'logical' ? 1 : 2,
        strokeDasharray: e.medium === 'wireless' ? '6 4' : e.medium === 'logical' ? '2 4' : undefined,
      },
      markerEnd: e.medium === 'logical' ? undefined : { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
    };
  });
  return { nodes, edges };
}

const CHECK_LABEL: Record<string, string> = { icmp: 'Ping', tcp: 'Port check', http: 'Web page', dns: 'DNS query' };

const SIDE_LABEL: Record<MapSide | 'auto', string> = { auto: 'Auto', top: 'Top', bottom: 'Bottom', left: 'Left', right: 'Right' };

function Details({ n, pin, onPin, onClose }: { n: MapNode; pin?: MapSide; onPin: (side: MapSide | null) => void; onClose: () => void }) {
  const st = STATE_STYLE[n.state];
  const i = n.info;
  const rows: [string, React.ReactNode][] = [
    ['State', <span className={st.text}>{st.label}</span>],
    ['IP', i.ip],
    ['Address', i.host],
    ['Check', i.check && (CHECK_LABEL[i.check] ?? i.check)],
    ['Where', n.kind === 'remote' && 'Outside your network'],
    ['MAC', i.mac && <span className="font-mono">{i.mac}</span>],
    ['Model', i.model],
    ['Role', i.role && (i.role === 'main' ? 'Main Deco' : 'Satellite')],
    ['Backhaul', i.backhaul],
    ['Firmware', i.firmware],
    ['Type', i.type && (DEVICE_TYPE_LABEL[i.type] ?? i.type)],
    ['Vendor', i.vendor],
    ['Connection', i.connection],
    ['Group', i.group && <span style={{ color: colourOf(i.group.colour).hex }}>{i.group.name}</span>],
    ['Tags', i.tags && i.tags.length > 0 && i.tags.map((t) => `#${t}`).join(' ')],
    ['Uptime', i.uptime_s != null && fmtDurationMs(i.uptime_s * 1000)],
    ['Fibre signal', i.rx_dbm != null && `${i.rx_dbm} dBm received${i.tx_dbm != null ? `, ${i.tx_dbm} dBm sent` : ''}`],
    ['Internet', i.wan_up != null && (i.wan_up ? `up${i.wan_ip ? ` · ${i.wan_ip}` : ''}` : 'down')],
  ];
  return (
    <aside className="space-y-3 rounded-lg bg-slate-900 p-4 text-sm ring-1 ring-slate-800" aria-label="Node details">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-semibold text-white">{ICON[n.kind]} {n.label}</h2>
        <button className="text-slate-500 hover:text-slate-300" onClick={onClose} aria-label="Close details">✕</button>
      </div>
      {n.inferred && <p className="text-xs text-slate-500">Not monitored directly: its state is inferred from what sits behind it.</p>}
      <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-y-1">
        {rows.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="break-words text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
      <label className="flex items-center justify-between gap-2 text-xs text-slate-400">
        Links attach to
        <select aria-label="Links attach to" className={input} value={pin ?? 'auto'}
          onChange={(e) => onPin(e.target.value === 'auto' ? null : (e.target.value as MapSide))}>
          {(Object.keys(SIDE_LABEL) as (MapSide | 'auto')[]).map((k) => <option key={k} value={k}>{SIDE_LABEL[k]}</option>)}
        </select>
      </label>
      {i.interfaces && i.interfaces.length > 0 && (
        <p className="text-xs text-slate-400">
          {i.interfaces.map((x) => `${x.name}: ${x.state ?? '?'}${x.speed ? ` @ ${x.speed} Mb/s` : ''}`).join(' · ')}
        </p>
      )}
      {n.members.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs uppercase tracking-wide text-slate-500">Monitored</h3>
          <ul className="space-y-1">
            {n.members.map((m) => (
              <li key={m.target_id} className="flex flex-wrap items-center gap-x-2">
                <span className={`h-2 w-2 rounded-full ${STATE_STYLE[m.state ?? 'unknown'].dot}`} />
                <Link to={`/targets/${m.target_id}`} className="text-sky-400 hover:underline">{m.name}</Link>
                <span className="text-xs text-slate-400">{fmtMs(m.median_ms)} · <span style={{ color: lossColour(m.loss_pct) }}>{fmtPct(m.loss_pct)}</span> loss</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {i.devices && (
        <ul className="max-h-72 space-y-1 overflow-y-auto">
          {i.devices.map((d) => (
            <li key={d.device_id} className="flex justify-between gap-2">
              <Link to={`/devices/${d.device_id}`} className="truncate text-sky-400 hover:underline">{d.label}</Link>
              <span className="shrink-0 text-xs text-slate-500">{[d.connection, d.ip].filter(Boolean).join(' · ')}</span>
            </li>
          ))}
        </ul>
      )}
      {i.device_id != null && (
        <Link to={`/devices/${i.device_id}`} className="inline-block text-sky-400 hover:underline">Open device</Link>
      )}
      {n.kind === 'remote' && i.target_id != null && (
        <Link to={`/targets/${i.target_id}`} className="inline-block text-sky-400 hover:underline">Open server page (history, SSH, admin page)</Link>
      )}
    </aside>
  );
}

function fmtLocalInput(ms: number) {
  const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
  return d.toISOString().slice(0, 16);
}

export function NetworkMap() {
  const [params, setParams] = useSearchParams();
  const incidentParam = params.get('incident') ?? undefined;
  const eventParam = params.get('event') ? Number(params.get('event')) : undefined;
  const atParam = params.get('at') ? Number(params.get('at')) : undefined;
  const replay = Boolean(incidentParam || eventParam || atParam);

  const [graph, setGraph] = useState<NetworkMapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<MapPositions>({});
  const [pins, setPins] = useState<MapPins>({});
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [colourBy, setColourBy] = useState<ColourBy>('group');
  const [groupFilter, setGroupFilter] = useState<number[]>([]);
  const [tagFilter, setTagFilter] = useState('');
  const [showOffline, setShowOffline] = useState(false);
  const [views, setViews] = useState<MapView[]>([]);
  const [viewName, setViewName] = useState('');
  const [layoutReady, setLayoutReady] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const flowRef = useRef<{ fitView: (o?: { padding?: number; duration?: number }) => unknown } | null>(null);
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const pinsRef = useRef(pins);
  pinsRef.current = pins;
  // Full screen: the map fills the window (and the screen, where the browser allows it).
  // ?full=1 opens straight into it, for a wall tablet bookmark.
  const [full, setFull] = useState(params.get('full') === '1');
  const rootRef = useRef<HTMLDivElement | null>(null);
  const usedApi = useRef(false);

  useEffect(() => {
    api.mapLayout().then((r) => {
      setSaved(r.positions);
      setPins(r.pins ?? {});
      setCollapsed(r.collapsed ?? []);
    }, () => undefined).finally(() => setLayoutReady(true));
    api.mapViews().then((r) => setViews(r.views ?? []), () => undefined);
  }, []);

  useEffect(() => {
    if (!replay) return;
    api.diagnosis(Math.floor((Date.now() - REPLAY_SPAN_MS) / 1000)).then((d) => setIncidents(d.incidents), () => undefined);
  }, [replay]);

  const load = useCallback(async () => {
    try {
      setGraph(await api.map({ at: atParam, incident: incidentParam, event: eventParam, show_offline: showOffline }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [atParam, incidentParam, eventParam, showOffline]);

  useEffect(() => {
    void load();
    if (replay) return;
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 15_000);
    return () => clearInterval(t);
  }, [load, replay]);

  const shaped = useMemo(
    () => (graph ? shapeGraph(graph, { colourBy, groups: groupFilter, tags: tagFilter ? [tagFilter] : [], collapsed }) : null),
    [graph, colourBy, groupFilter, tagFilter, collapsed],
  );
  // Recompute positions only when the set of nodes changes, so polling does not undo a drag.
  const shape = shaped ? shaped.nodes.map((n) => n.id).sort().join('|') + '#' + shaped.edges.map((e) => e.id).sort().join('|') : '';
  const positions = useMemo(
    () => (shaped ? layoutMap(shaped.nodes, shaped.edges, saved) : {}),
    [shape, saved],
  );

  // Which side each link uses, from where the tiles are right now (so it follows a drag) and
  // the pins. Recomputed from the tiles' live positions, not from the saved layout.
  const linked = useMemo(() => {
    const at = new Map(nodes.map((n) => [n.id, n.position]));
    return edges.map((e) => {
      const a = at.get(e.source);
      const b = at.get(e.target);
      if (!a || !b) return e;
      return { ...e, ...edgeHandles(a, b, pins[e.source], pins[e.target]) };
    });
  }, [nodes, edges, pins]);

  useEffect(() => {
    if (!graph || !shaped || !layoutReady) return;
    const flow = toFlow(shaped, positions, graph.highlight, shaped.accent, shaped.faded);
    setNodes((cur) => {
      const where = new Map(cur.map((n) => [n.id, n.position]));
      return flow.nodes.map((n) => ({ ...n, position: where.get(n.id) ?? n.position, selected: n.id === selected }));
    });
    setEdges(flow.edges);
  }, [graph, shaped, positions, layoutReady, selected, setNodes, setEdges]);

  const enterFull = useCallback(() => {
    setFull(true);
    setParams((p) => { const q = new URLSearchParams(p); q.set('full', '1'); return q; }, { replace: true });
    const el = rootRef.current;
    if (el?.requestFullscreen && !document.fullscreenElement) {
      el.requestFullscreen().then(() => { usedApi.current = true; }, () => { /* the window-filling view is enough */ });
    }
  }, [setParams]);
  const exitFull = useCallback(() => {
    setFull(false);
    setParams((p) => { const q = new URLSearchParams(p); q.delete('full'); return q; }, { replace: true });
    if (usedApi.current && document.fullscreenElement && document.exitFullscreen) void document.exitFullscreen().catch(() => {});
    usedApi.current = false;
  }, [setParams]);

  // F toggles full screen (not while typing); Esc leaves it. When the browser leaves its own
  // full screen mode (its Esc), leave ours too.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName));
      if (e.key === 'Escape' && full) exitFull();
      else if ((e.key === 'f' || e.key === 'F') && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (full) exitFull();
        else enterFull();
      }
    };
    const onChange = () => { if (!document.fullscreenElement && usedApi.current) exitFull(); };
    window.addEventListener('keydown', onKey);
    document.addEventListener('fullscreenchange', onChange);
    return () => { window.removeEventListener('keydown', onKey); document.removeEventListener('fullscreenchange', onChange); };
  }, [full, enterFull, exitFull]);

  useEffect(() => {
    const t = setTimeout(() => void flowRef.current?.fitView({ padding: 0.15, duration: 300 }), 120);
    return () => clearTimeout(t);
  }, [full]);

  // When nodes appear or disappear (a group folded or opened, offline devices shown), fit the
  // whole map in view again. Not on every poll: only when the shape changes.
  useEffect(() => {
    const t = setTimeout(() => void flowRef.current?.fitView({ padding: 0.15, duration: 300 }), 60);
    return () => clearTimeout(t);
  }, [shape]);

  const toggleCollapse = (gid: number) => {
    const key = String(gid);
    const next = collapsed.includes(key) ? collapsed.filter((c) => c !== key) : [...collapsed, key];
    setCollapsed(next);
    api.saveMapLayout(savedRef.current, next).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  const toggleGroupFilter = (gid: number) =>
    setGroupFilter((cur) => (cur.includes(gid) ? cur.filter((x) => x !== gid) : [...cur, gid]));
  const applyView = (v: MapView) => {
    setColourBy(v.colour_by);
    setGroupFilter(v.groups);
    setTagFilter(v.tags[0] ?? '');
    setShowOffline(v.show_offline);
    setCollapsed(v.collapsed);
    api.saveMapLayout(savedRef.current, v.collapsed).catch(() => undefined);
  };
  const saveViews = (next: MapView[]) =>
    api.saveMapViews(next).then((r) => setViews(r.views), (e) => setError(e instanceof Error ? e.message : String(e)));
  const saveView = () => {
    const name = viewName.trim();
    if (!name) return;
    const v: MapView = { name, colour_by: colourBy, groups: groupFilter, tags: tagFilter ? [tagFilter] : [], collapsed, show_offline: showOffline };
    void saveViews([...views.filter((x) => x.name.toLowerCase() !== name.toLowerCase()), v]);
    setViewName('');
  };
  const mapTags = [...new Set((graph?.nodes ?? []).flatMap((n) => n.info.tags ?? []))].sort();
  const mapGroups = graph?.groups ?? [];

  const onDragStop = useCallback((_: unknown, _n: FlowNode, dragged: FlowNode[]) => {
    const next = { ...savedRef.current };
    for (const d of dragged) next[d.id] = { x: Math.round(d.position.x), y: Math.round(d.position.y) };
    setSaved(next);
    api.saveMapLayout(next).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const setPin = useCallback((id: string, side: MapSide | null) => {
    const next = { ...pinsRef.current };
    if (side) next[id] = side;
    else delete next[id];
    setPins(next);
    api.saveMapLayout(savedRef.current, undefined, next).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function resetLayout() {
    await api.resetMapLayout().catch(() => undefined);
    setSaved({});
    setPins({});
    setNodes([]);
  }

  const setReplay = (p: Record<string, string>) => setParams(full ? { ...p, full: '1' } : p, { replace: true });
  const now = Date.now();
  const at = graph?.at_ms ?? atParam ?? graph?.highlight?.start_ms ?? now;
  const node = shaped?.nodes.find((n) => n.id === selected) ?? null;
  const hl = graph?.highlight;

  return (
    <div ref={rootRef} data-testid="map-page" aria-label={full ? 'Network map, full screen' : undefined}
      className={full ? 'fixed inset-0 z-50 flex flex-col gap-2 overflow-hidden bg-slate-950 p-2 sm:p-3' : 'space-y-4'}>
      <div className={`flex flex-wrap items-center justify-between ${full ? 'gap-2' : 'gap-3'}`}>
        <h1 className={full ? 'text-sm font-semibold text-slate-300' : 'text-xl font-semibold text-white'}>Network map</h1>
        <div className="flex flex-wrap items-center gap-2">
          <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={full ? exitFull : enterFull}
            title={full ? 'Leave full screen (Esc or F)' : 'Full screen (F)'} aria-pressed={full}>
            {full ? 'Exit full screen' : 'Full screen'}
          </button>
          <div className="inline-flex overflow-hidden rounded-md ring-1 ring-slate-700" role="group" aria-label="Mode">
            <button className={`${btn} rounded-none ${!replay ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
              onClick={() => setReplay({})} aria-pressed={!replay}>Live</button>
            <button className={`${btn} rounded-none ${replay ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
              onClick={() => !replay && setReplay({ at: String(now - 3600_000) })} aria-pressed={replay}>Replay</button>
          </div>
          <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => void resetLayout()}
            disabled={Object.keys(saved).length === 0 && Object.keys(pins).length === 0} title="Forget dragged positions and link sides">Reset layout</button>
        </div>
      </div>

      <section className="flex flex-wrap items-center gap-2 text-sm" aria-label="Map options">
        <label className="flex items-center gap-1.5 text-slate-300">
          Colour by
          <select aria-label="Colour by" className={input} value={colourBy} onChange={(e) => setColourBy(e.target.value as ColourBy)}>
            <option value="group">group</option>
            <option value="status">status only</option>
            <option value="bandwidth">bandwidth</option>
          </select>
        </label>
        {mapTags.length > 0 && (
          <select aria-label="Tag filter" className={input} value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
            <option value="">All tags</option>
            {mapTags.map((t) => <option key={t} value={t}>#{t}</option>)}
          </select>
        )}
        <label className="flex items-center gap-1.5 text-slate-300">
          <input type="checkbox" checked={showOffline} onChange={(e) => setShowOffline(e.target.checked)} />
          Show offline (24 h)
        </label>
        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
          <select aria-label="Saved views" className={input} value="" onChange={(e) => { const v = views.find((x) => x.name === e.target.value); if (v) applyView(v); }}>
            <option value="">Views…</option>
            {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
          <input aria-label="View name" placeholder="Save view as…" className={`${input} w-36`} maxLength={40} value={viewName}
            onChange={(e) => setViewName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && saveView()} />
          <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={!viewName.trim()} onClick={saveView}>Save</button>
        </span>
      </section>
      {mapGroups.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Groups on the map">
          {mapGroups.map((g) => {
            const c = colourOf(g.colour);
            const on = groupFilter.includes(g.id);
            const isCollapsed = collapsed.includes(String(g.id));
            return (
              <li key={g.id} className={`inline-flex items-center overflow-hidden rounded-full text-xs ring-1 ${c.chip} ${groupFilter.length && !on ? 'opacity-50' : ''}`}>
                <button className="inline-flex items-center gap-1 py-0.5 pl-2 pr-1" aria-pressed={on} onClick={() => toggleGroupFilter(g.id)}
                  title={on ? 'Stop highlighting' : 'Highlight only this group'}>
                  <GroupIcon icon={g.icon} className="h-3 w-3" /> {g.name} <span className="opacity-70">{g.nodes.length}</span>
                </button>
                <button className="border-l border-current/20 px-1.5 py-0.5 hover:bg-white/10" onClick={() => toggleCollapse(g.id)}
                  aria-label={isCollapsed ? `Expand ${g.name}` : `Collapse ${g.name}`} title={isCollapsed ? 'Show each device' : 'Show as one node'}>
                  {isCollapsed ? '▸' : '▾'}
                </button>
              </li>
            );
          })}
          {views.length > 0 && <li className="ml-auto text-xs text-slate-500">
            {views.map((v) => (
              <button key={v.name} className="ml-2 hover:text-rose-300" title={`Delete the view ${v.name}`}
                onClick={() => void saveViews(views.filter((x) => x.name !== v.name))}>✕ {v.name}</button>
            ))}
          </li>}
        </ul>
      )}

      {replay && (
        <section className="space-y-2 rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800" aria-label="Replay">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-slate-300">
              Incident
              <select aria-label="Incident" className={`${input} max-w-[16rem]`} value={hl?.incident_id ?? ''}
                onChange={(e) => setReplay(e.target.value ? { incident: e.target.value } : { at: String(at) })}>
                <option value="">— none —</option>
                {incidents.map((i) => (
                  <option key={i.id} value={i.id}>{fmtTime(i.start_ms)} · {i.causes[0]?.title ?? 'incident'}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-slate-300">
              Time
              <input type="datetime-local" aria-label="Replay time" className={input} value={fmtLocalInput(at)}
                max={fmtLocalInput(now)} onChange={(e) => {
                  const ms = new Date(e.target.value).getTime();
                  if (Number.isFinite(ms)) setReplay({ at: String(Math.min(ms, now)) });
                }} />
            </label>
          </div>
          <input type="range" aria-label="Replay slider" className="w-full accent-sky-500" min={now - REPLAY_SPAN_MS} max={now}
            step={60_000} value={at} onChange={(e) => setReplay({ at: e.target.value })} />
          <p className="text-xs text-slate-400">
            Showing {fmtTime(at)}: state from recorded outages, loss and Wi-Fi events, latency averaged over ±1 min.
          </p>
          {hl?.cause && (
            <p className="text-sm text-rose-200">
              Likely cause: <span className="font-medium">{hl.cause.title}</span> ({hl.cause.confidence} %) ·{' '}
              <Link to="/diagnosis" className="text-sky-400 hover:underline">Diagnosis</Link>
            </p>
          )}
        </section>
      )}

      {error && <p className="text-rose-400">{error}</p>}
      {graph && !graph.at_ms && !full && <DecoNote graph={graph} />}

      <div className={full ? 'relative min-h-0 flex-1' : 'grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]'}>
        <div className={`overflow-hidden rounded-lg bg-slate-950 ring-1 ring-slate-800 ${full ? 'h-full' : 'h-[65vh] min-h-[420px]'}`} data-testid="map-canvas">
          {graph && layoutReady && (
            <ReactFlow
              nodes={nodes}
              edges={linked}
              connectionMode={ConnectionMode.Loose}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onDragStop}
              onNodeClick={(_, n) => setSelected(n.id)}
              onPaneClick={() => setSelected(null)}
              nodesConnectable={false}
              edgesFocusable={false}
              colorMode="dark"
              fitView
              fitViewOptions={{ padding: 0.15 }}
              onInit={(inst) => { flowRef.current = inst; }}
              minZoom={0.2}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} color="#1e293b" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>
        <div className={full ? 'absolute right-2 top-2 z-10 max-h-[calc(100%-1rem)] w-[min(20rem,calc(100%-1rem))] overflow-y-auto' : 'space-y-3'}>
          {node ? (
            <Details n={node} pin={pins[node.id]} onPin={(side) => setPin(node.id, side)} onClose={() => setSelected(null)} />
          ) : full ? null : (
            <div className="rounded-lg bg-slate-900 p-4 text-sm text-slate-400 ring-1 ring-slate-800">
              {!replay && graph?.deco_api && <div className="mb-4"><TopTalkers limit={5} compact /></div>}
              <p>Tap a node for details. Drag nodes to tidy the map; positions are saved. Group chips above the map highlight a group or fold it into one node.</p>
              <ul className="mt-3 space-y-1 text-xs">
                <li><span className="inline-block w-8 border-t-2 border-slate-500 align-middle" /> wired</li>
                <li><span className="inline-block w-8 border-t-2 border-dashed border-slate-500 align-middle" /> Wi-Fi</li>
                <li><span className="inline-block w-8 border-t border-dotted border-slate-500 align-middle" /> logical (not measured)</li>
                <li>Line colour: packet loss (green → red); labels show median latency and live traffic; busy links are thicker.</li>
              </ul>
            </div>
          )}
        </div>
      </div>
      {graph && !full && <p className="text-xs text-slate-500">Updated {fmtTime(graph.generated_ms)}</p>}
    </div>
  );
}

/** Why devices may all be shown under the Home Hub: the Deco link is off, not polling, paused or not read yet. */
function DecoNote({ graph }: { graph: NetworkMapResponse }) {
  const state = graph.deco?.state ?? (graph.deco_api ? 'ok' : 'off');
  if (state === 'ok') return null;
  const admin = <Link to="/admin" className="text-sky-400 hover:underline">Admin → Deco mesh</Link>;
  const warn = 'rounded-md bg-amber-950/40 p-2 text-xs text-amber-200 ring-1 ring-amber-900';
  if (state === 'not_polling') {
    return <p role="status" className={warn}>Deco polling is off, so every device is shown under the Home Hub. Turn on polling in {admin}.</p>;
  }
  if (state === 'paused') {
    return <p role="status" className={warn}>Deco polling stopped ({graph.deco?.paused}), so devices are shown under the Home Hub. Save the settings in {admin} to resume.</p>;
  }
  if (state === 'waiting') return <p role="status" className="text-xs text-slate-400">Waiting for the first Deco reading…</p>;
  return (
    <p className="text-xs text-slate-500">
      Decos and which device is on which Deco come from the Deco API: connect it under {admin}.
    </p>
  );
}

