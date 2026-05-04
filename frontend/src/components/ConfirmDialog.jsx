import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  icon = 'delete',
  busy = false,
  danger = false,
  onCancel,
  onConfirm,
}) {
  const titleId = useId();
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => cancelRef.current?.focus(), 0);
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) {
        onCancel?.();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open, busy, onCancel]);

  if (!open) return null;

  return createPortal(
    <div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <button
        type="button"
        className="confirm-dialog-scrim"
        aria-label="Close"
        disabled={busy}
        onClick={onCancel}
      />
      <div className="confirm-dialog-panel">
        <div className="confirm-dialog-icon" aria-hidden>
          <span className="home-ms">{icon}</span>
        </div>
        <h2 id={titleId} className="confirm-dialog-title">
          {title}
        </h2>
        {message ? <p className="confirm-dialog-message">{message}</p> : null}
        <div className="confirm-dialog-actions">
          <button
            ref={cancelRef}
            type="button"
            className="dashboard-btn-ghost"
            disabled={busy}
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`dashboard-btn-primary confirm-dialog-confirm${danger ? ' confirm-dialog-confirm--danger' : ''}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
