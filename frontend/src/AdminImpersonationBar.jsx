import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';

export function AdminImpersonationBar() {
  const navigate = useNavigate();
  const {
    actorUser,
    isAdmin,
    adminUsers,
    adminUsersLoading,
    impersonatedUser,
    setImpersonatedUser,
    refreshAdminUsers,
  } = useAuth();

  const switchUser = useCallback((event) => {
    const id = event.target.value;
    const nextUser = adminUsers.find((candidate) => candidate.id === id) || null;
    setImpersonatedUser(nextUser);
    navigate('/dashboard');
  }, [adminUsers, navigate, setImpersonatedUser]);

  if (!actorUser || !isAdmin) return null;

  const actorLabel = actorUser.email || actorUser.id;
  const effectiveLabel = impersonatedUser?.email || impersonatedUser?.id || actorLabel;

  return (
    <header className={`admin-impersonation-bar${impersonatedUser ? ' is-impersonating' : ''}`}>
      <div className="admin-impersonation-status">
        <span className="home-ms admin-impersonation-icon" aria-hidden>
          admin_panel_settings
        </span>
        <span className="admin-impersonation-copy">
          <strong>{impersonatedUser ? 'Acting as' : 'Admin mode'}</strong>
          <span title={effectiveLabel}>{effectiveLabel}</span>
        </span>
      </div>
      <div className="admin-impersonation-controls">
        <label className="admin-impersonation-label" htmlFor="admin-impersonation-user">
          View as
        </label>
        <select
          id="admin-impersonation-user"
          className="admin-impersonation-select"
          value={impersonatedUser?.id || ''}
          onChange={switchUser}
          disabled={adminUsersLoading}
        >
          <option value="">My account ({actorLabel})</option>
          {adminUsers.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.email || candidate.id}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="admin-impersonation-button"
          onClick={() => void refreshAdminUsers()}
          disabled={adminUsersLoading}
          aria-label="Refresh recent users"
          title="Refresh recent users"
        >
          <span className="home-ms" aria-hidden>
            refresh
          </span>
        </button>
        {impersonatedUser ? (
          <button
            type="button"
            className="admin-impersonation-exit"
            onClick={() => {
              setImpersonatedUser(null);
              navigate('/dashboard');
            }}
          >
            Exit user
          </button>
        ) : null}
      </div>
    </header>
  );
}
