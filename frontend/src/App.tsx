import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Overview } from './pages/Overview';
import { TargetDetail } from './pages/TargetDetail';
import { Targets } from './pages/Targets';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="targets" element={<Targets />} />
        <Route path="targets/:id" element={<TargetDetail />} />
        <Route path="*" element={<p className="text-slate-400">Page not found.</p>} />
      </Route>
    </Routes>
  );
}
