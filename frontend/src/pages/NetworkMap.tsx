import {
  Background,
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
import { api, type Incident, type MapEdge, type MapHighlight, type MapNode, type MapPositions, type NetworkMapResponse } from '../lib/api';
import { TopTalkers } from '../components/TopTalkers';
import { DEVICE_TYPE_LABEL, STATE_STYLE, fmtBps, fmtDurationMs, fmtMs, fmtPct, fmtTime, lossColour } from '../lib/format';
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
};

type MapNodeData = { node: MapNode; mark: 'cause' | 'failing' | null };
type FlowNode = Node<MapNodeData, 'net'>;

function rates(t: { down_bps: number; up_bps: number }): string {
  return `↓${fmtBps(t.down_bps)} ↑${fmtBps(t.up_bps)}`;
}

function subline(n: MapNode): string {
  if (n.traffic) return rates(n.traffic);
  if (n.kind === 'group') return 'tap to list';
  const m = n.members.find((x) => x.median_ms != null);
  const parts = [n.info.ip, n.info.backhaul && n.kind === 'deco' ? n.info.backhaul : null, m ? fmtMs(m.median_ms) : null];
  return parts.filter(Boolean).join(' · ') || (n.inferred ? 'inferred' : '');
}

function NetNode({ data, selected }: NodeProps<FlowNode>) {
  const { node: n, mark } = data;
  const st = STATE_STYLE[n.state];
  const ring =
    mark === 'cause' ? 'ring-4 ring-rose-500' : mark === 'failing' ? 'ring-2 ring-amber-400' : selected ? 'ring-2 ring-sky-500' : `ring-1 ${st.ring}`;
  return (
    <div
      className={`relative flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-left shadow ${ring} ${n.inferred ? 'border border-dashed border-slate-600' : ''}`}
      style={{ width: NODE_W, height: NODE_H }}
      data-testid={`node-${n.id}`}
    >
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-0 !bg-slate-600" />
      <span className="text-xl" aria-hidden>{ICON[n.kind]}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} title={st.label} />
          <span className="truncate text-sm font-medium text-slate-100">{n.label}</span>
        </div>
        <div className="truncate text-[11px] text-slate-400">{subline(n)}</div>
      </div>
      {mark === 'cause' && <span className="absolute -top-2 right-2 rounded bg-rose-600 px-1 text-[10px] text-white">likely cause</span>}
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-0 !bg-slate-600" />
    </div>
  );
}

const nodeTypes = { net: NetNode };

function edgeLabel(e: MapEdge): string | undefined {
  const stats = [e.median_ms != null ? fmtMs(e.median_ms) : null, e.loss_pct ? `${fmtPct(e.loss_pct)} loss` : null].filter(Boolean);
  const text = [e.label, ...stats, e.traffic && rates(e.traffic)].filter(Boolean).join(' · ');
  return text || undefined;
}

function toFlow(g: NetworkMapResponse, pos: MapPositions, hl: MapHighlight | undefined): { nodes: FlowNode[]; edges: Edge[] } {
  const cause = new Set(hl?.cause_nodes ?? []);
  const failing = new Set(hl?.failing_nodes ?? []);
  const nodes: FlowNode[] = g.nodes.map((n) => ({
    id: n.id,
    type: 'net',
    position: pos[n.id] ?? { x: 0, y: 0 },
    data: { node: n, mark: cause.has(n.id) ? 'cause' : failing.has(n.id) ? 'failing' : null },
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

function Details({ n, onClose }: { n: MapNode; onClose: () => void }) {
  const st = STATE_STYLE[n.state];
  const i = n.info;
  const rows: [string, React.ReactNode][] = [
    ['State', <span className={st.text}>{st.label}</span>],
    ['IP', i.ip],
    ['MAC', i.mac && <span className="font-mono">{i.mac}</span>],
    ['Model', i.model],
    ['Role', i.role && (i.role === 'main' ? 'Main Deco' : 'Satellite')],
    ['Backhaul', i.backhaul],
    ['Firmware', i.firmware],
    ['Type', i.type && (DEVICE_TYPE_LABEL[i.type] ?? i.type)],
    ['Vendor', i.vendor],
    ['Connection', i.connection],
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
      <dl className="grid grid-cols-[6.5rem_1fr] gap-y-1">
        {rows.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="break-words text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
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
  const [layoutReady, setLayoutReady] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const savedRef = useRef(saved);
  savedRef.current = saved;

  useEffect(() => {
    api.mapLayout().then((r) => setSaved(r.positions), () => undefined).finally(() => setLayoutReady(true));
  }, []);

  useEffect(() => {
    if (!replay) return;
    api.diagnosis(Math.floor((Date.now() - REPLAY_SPAN_MS) / 1000)).then((d) => setIncidents(d.incidents), () => undefined);
  }, [replay]);

  const load = useCallback(async () => {
    try {
      setGraph(await api.map({ at: atParam, incident: incidentParam, event: eventParam }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [atParam, incidentParam, eventParam]);

  useEffect(() => {
    void load();
    if (replay) return;
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 15_000);
    return () => clearInterval(t);
  }, [load, replay]);

  // Recompute positions only when the set of nodes changes, so polling does not undo a drag.
  const shape = graph ? graph.nodes.map((n) => n.id).sort().join('|') + '#' + graph.edges.map((e) => e.id).sort().join('|') : '';
  const positions = useMemo(
    () => (graph ? layoutMap(graph.nodes, graph.edges, saved) : {}),
    [shape, saved],
  );

  useEffect(() => {
    if (!graph || !layoutReady) return;
    const flow = toFlow(graph, positions, graph.highlight);
    setNodes((cur) => {
      const where = new Map(cur.map((n) => [n.id, n.position]));
      return flow.nodes.map((n) => ({ ...n, position: where.get(n.id) ?? n.position, selected: n.id === selected }));
    });
    setEdges(flow.edges);
  }, [graph, positions, layoutReady, selected, setNodes, setEdges]);

  const onDragStop = useCallback((_: unknown, _n: FlowNode, dragged: FlowNode[]) => {
    const next = { ...savedRef.current };
    for (const d of dragged) next[d.id] = { x: Math.round(d.position.x), y: Math.round(d.position.y) };
    setSaved(next);
    api.saveMapLayout(next).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function resetLayout() {
    await api.resetMapLayout().catch(() => undefined);
    setSaved({});
    setNodes([]);
  }

  const setReplay = (p: Record<string, string>) => setParams(p, { replace: true });
  const now = Date.now();
  const at = graph?.at_ms ?? atParam ?? graph?.highlight?.start_ms ?? now;
  const node = graph?.nodes.find((n) => n.id === selected) ?? null;
  const hl = graph?.highlight;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-white">Network map</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex overflow-hidden rounded-md ring-1 ring-slate-700" role="group" aria-label="Mode">
            <button className={`${btn} rounded-none ${!replay ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
              onClick={() => setReplay({})} aria-pressed={!replay}>Live</button>
            <button className={`${btn} rounded-none ${replay ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
              onClick={() => !replay && setReplay({ at: String(now - 3600_000) })} aria-pressed={replay}>Replay</button>
          </div>
          <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => void resetLayout()}
            disabled={Object.keys(saved).length === 0} title="Forget dragged positions">Reset layout</button>
        </div>
      </div>

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
      {graph && !graph.deco_api && (
        <p className="text-xs text-slate-500">
          Decos and which device is on which Deco come from the Deco API: connect it under{' '}
          <Link to="/admin" className="text-sky-400 hover:underline">Admin → Deco mesh</Link>.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="h-[65vh] min-h-[420px] overflow-hidden rounded-lg bg-slate-950 ring-1 ring-slate-800" data-testid="map-canvas">
          {graph && layoutReady && (
            <ReactFlow
              nodes={nodes}
              edges={edges}
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
              minZoom={0.2}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} color="#1e293b" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>
        <div className="space-y-3">
          {node ? (
            <Details n={node} onClose={() => setSelected(null)} />
          ) : (
            <div className="rounded-lg bg-slate-900 p-4 text-sm text-slate-400 ring-1 ring-slate-800">
              {!replay && graph?.deco_api && <div className="mb-4"><TopTalkers limit={5} compact /></div>}
              <p>Tap a node for details. Drag nodes to tidy the map; positions are saved.</p>
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
      {graph && <p className="text-xs text-slate-500">Updated {fmtTime(graph.generated_ms)}</p>}
    </div>
  );
}
