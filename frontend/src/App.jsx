import { useEffect } from 'react';
import { Route, Routes, Navigate, useNavigate } from 'react-router-dom';
import { StrategyPage } from './pages/StrategyPage';
import { HomePage } from './pages/HomePage';
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
      navigate(user ? '/' : '/', { replace: true });
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
    </Routes>
  );
}
