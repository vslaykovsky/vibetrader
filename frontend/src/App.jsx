import { useEffect } from 'react';
import { Route, Routes, Navigate, useNavigate } from 'react-router-dom';
import { StrategyPage } from './pages/StrategyPage';
import { HomePage } from './pages/HomePage';
import { LiveRunsPage } from './pages/LiveRunsPage';
import { LiveRunStreamPage } from './pages/LiveRunStreamPage';
import { DashboardPage } from './pages/DashboardPage';
import { TradingSettingsPage } from './pages/TradingSettingsPage';
import { useAuth } from './AuthContext';

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/" replace />;
  return children;
}

function AuthCallback() {
  const navigate = useNavigate();
  const { loading, user } = useAuth();

  useEffect(() => {
    if (!loading) {
      navigate(user ? '/dashboard' : '/', { replace: true });
    }
  }, [loading, user, navigate]);

  return null;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="/strategy/:threadId" element={<ProtectedRoute><StrategyPage /></ProtectedRoute>} />
      <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
      <Route path="/dashboard/settings" element={<ProtectedRoute><TradingSettingsPage /></ProtectedRoute>} />
      <Route path="/live" element={<ProtectedRoute><LiveRunsPage /></ProtectedRoute>} />
      <Route path="/live/:runId" element={<ProtectedRoute><LiveRunStreamPage /></ProtectedRoute>} />
    </Routes>
  );
}
