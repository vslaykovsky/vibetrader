import { Route, Routes } from 'react-router-dom';
import { StrategyPage } from './pages/StrategyPage';
import { HomePage } from './pages/HomePage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/strategy/:threadId" element={<StrategyPage />} />
    </Routes>
  );
}
