import { Navigate } from 'react-router-dom';

export function LiveRunsPage() {
  return <Navigate to="/dashboard#live-deployments" replace />;
}
