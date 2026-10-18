import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Admin } from './pages/Admin';
import { DeviceDetail } from './pages/DeviceDetail';
import { Devices } from './pages/Devices';
import { Diagnosis } from './pages/Diagnosis';
import { Diagnostics } from './pages/Diagnostics';
import { EventDetail } from './pages/EventDetail';
import { Health } from './pages/Health';
import { Exposure } from './pages/Exposure';
import { Overview } from './pages/Overview';
import { Report } from './pages/Report';
import { System } from './pages/System';
import { TargetDetail } from './pages/TargetDetail';
import { Targets } from './pages/Targets';
import { Timeline } from './pages/Timeline';

// React Flow is large: load it only when the map is opened.
const NetworkMap = lazy(() => import('./pages/NetworkMap').then((m) => ({ default: m.NetworkMap })));

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="targets" element={<Targets />} />
        <Route path="targets/:id" element={<TargetDetail />} />
        <Route path="devices" element={<Devices />} />
        <Route path="devices/:id" element={<DeviceDetail />} />
        <Route path="exposure" element={<Exposure />} />
        <Route path="health" element={<Health />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="events/:id" element={<EventDetail />} />
        <Route path="map" element={<Suspense fallback={<p className="text-slate-400">Loading map…</p>}><NetworkMap /></Suspense>} />
        <Route path="diagnosis" element={<Diagnosis />} />
        <Route path="report" element={<Report />} />
        <Route path="diagnostics" element={<Diagnostics />} />
        <Route path="system" element={<System />} />
        <Route path="admin" element={<Admin />} />
        <Route path="*" element={<p className="text-slate-400">Page not found.</p>} />
      </Route>
    </Routes>
  );
}
