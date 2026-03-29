import { Navigate, Route, Routes } from 'react-router-dom';
import { StrategyPage } from './pages/StrategyPage';

function createThreadId() {
  return crypto.randomUUID();
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to={`/strategy/${createThreadId()}`} replace />} />
      <Route path="/strategy/:threadId" element={<StrategyPage />} />
    </Routes>
  );
}
