import { Navigate, Route, Routes } from 'react-router-dom';
import { randomUUID } from './randomUUID.js';
import { StrategyPage } from './pages/StrategyPage';

function createThreadId() {
  return randomUUID();
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to={`/strategy/${createThreadId()}`} replace />} />
      <Route path="/strategy/:threadId" element={<StrategyPage />} />
    </Routes>
  );
}
