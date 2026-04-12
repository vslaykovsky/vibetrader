import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

export function ProfileMenu({ user, signOut, surface = 'strategy' }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const menuId = useId();

  const close = useCallback(() => setOpen(false), []);

  const updatePosition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setCoords({ top: r.bottom + 8, left: r.right });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setCoords(null);
      return undefined;
    }
    updatePosition();
    window.addEventListener('scroll', updatePosition, true);
    window.addEventListener('resize', updatePosition);
    return () => {
      window.removeEventListener('scroll', updatePosition, true);
      window.removeEventListener('resize', updatePosition);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      const t = e.target;
      if (triggerRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      close();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);

  const avatarUrl = user?.user_metadata?.avatar_url;
  const label = String(user?.user_metadata?.full_name || user?.email || 'Account').trim() || 'Account';

  const dropdown =
    open && coords ? (
      <div
        ref={menuRef}
        id={menuId}
        role="menu"
        className={
          surface === 'home'
            ? 'profile-menu-dropdown profile-menu-dropdown--home'
            : 'profile-menu-dropdown'
        }
        style={{
          position: 'fixed',
          top: coords.top,
          left: coords.left,
          transform: 'translateX(-100%)',
          zIndex: 200,
        }}
      >
        <button
          type="button"
          role="menuitem"
          className={
            surface === 'home'
              ? 'profile-menu-item profile-menu-item--home'
              : 'profile-menu-item'
          }
          onClick={() => {
            close();
            signOut();
          }}
        >
          Sign out
        </button>
      </div>
    ) : null;

  return (
    <>
      <div className="profile-menu-root">
        <button
          ref={triggerRef}
          type="button"
          className="profile-menu-trigger"
          aria-expanded={open}
          aria-haspopup="menu"
          aria-controls={open ? menuId : undefined}
          onClick={() => setOpen((o) => !o)}
          title={label}
          aria-label="Account menu"
        >
          {avatarUrl ? (
            <img className="auth-avatar" src={avatarUrl} alt="" />
          ) : (
            <span
              className={
                surface === 'home'
                  ? 'profile-menu-placeholder profile-menu-placeholder--home'
                  : 'profile-menu-placeholder'
              }
              aria-hidden
            >
              {label.charAt(0).toUpperCase()}
            </span>
          )}
        </button>
      </div>
      {dropdown ? createPortal(dropdown, document.body) : null}
    </>
  );
}
