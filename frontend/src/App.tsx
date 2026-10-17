import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Diagnostics } from './pages/Diagnostics';
import { EventDetail } from './pages/EventDetail';
import { Overview } from './pages/Overview';
import { System } from './pages/System';
import { TargetDetail } from './pages/TargetDetail';
import { Targets } from './pages/Targets';
import { Timeline } from './pages/Timeline';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="targets" element={<Targets />} />
        <Route path="targets/:id" element={<TargetDetail />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="events/:id" element={<EventDetail />} />
        <Route path="diagnostics" element={<Diagnostics />} />
        <Route path="system" element={<System />} />
        <Route path="*" element={<p className="text-slate-400">Page not found.</p>} />
      </Route>
    </Routes>
  );
}
