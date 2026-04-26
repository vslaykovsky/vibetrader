import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const MONTH_NAMES_FULL = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
];
const MONTH_SHORT = [
  'Янв',
  'Фев',
  'Мар',
  'Апр',
  'Май',
  'Июн',
  'Июл',
  'Авг',
  'Сен',
  'Окт',
  'Ноя',
  'Дек',
];
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

function pad2(n) {
  return String(n).padStart(2, '0');
}

/** @param {Date} d */
function toIsoDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function parseIsoDate(s) {
  const t = String(s || '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return null;
  const [y, m, d] = t.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
  return dt;
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0).getDate();
  const jsDow = first.getDay();
  const leading = (jsDow + 6) % 7;
  const cells = [];
  for (let i = 0; i < leading; i += 1) cells.push({ type: 'pad' });
  for (let day = 1; day <= lastDay; day += 1) cells.push({ type: 'day', day });
  return cells;
}

function decadeStartYear(year) {
  return Math.floor(year / 20) * 20;
}

/**
 * Модальное окно даты/времени в духе TradingView «Перейти к…» (без «Первый день с данными»).
 *
 * @param {{
 *   open: boolean;
 *   value: string;
 *   onClose: () => void;
 *   onConfirm: (isoDate: string) => void;
 *   disabled?: boolean;
 *   minDate?: string;
 *   maxDate?: string;
 * }} props
 */
export function DateTimePickerModal({
  open,
  value,
  onClose,
  onConfirm,
  disabled = false,
  minDate = '1990-01-01',
  maxDate,
}) {
  const overlayRef = useRef(null);
  const [view, setView] = useState('days');
  const [monthCursor, setMonthCursor] = useState(() => new Date());
  const [decade, setDecade] = useState(() => decadeStartYear(new Date().getFullYear()));
  const [selected, setSelected] = useState(() => new Date());
  const [timeStr, setTimeStr] = useState('00:00');

  const maxD = useMemo(() => {
    const raw = maxDate || toIsoDate(new Date());
    return parseIsoDate(raw) || new Date();
  }, [maxDate]);

  const minD = useMemo(() => parseIsoDate(minDate) || new Date(1990, 0, 1), [minDate]);

  const syncFromValue = useCallback(() => {
    const p = parseIsoDate(value);
    const base = p || new Date();
    const sd = startOfDay(base);
    setSelected(sd);
    setMonthCursor(new Date(sd.getFullYear(), sd.getMonth(), 1));
    setDecade(decadeStartYear(sd.getFullYear()));
    setView('days');
    setTimeStr('00:00');
  }, [value]);

  useEffect(() => {
    if (open) syncFromValue();
  }, [open, syncFromValue]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const y = monthCursor.getFullYear();
  const m = monthCursor.getMonth();

  const dayCells = useMemo(() => monthGrid(y, m), [y, m]);

  const isDayDisabled = (day) => {
    const d = new Date(y, m, day);
    const t = startOfDay(d).getTime();
    return t < startOfDay(minD).getTime() || t > startOfDay(maxD).getTime();
  };

  const isMonthDisabled = (monthIndex) => {
    const first = new Date(y, monthIndex, 1);
    const last = new Date(y, monthIndex + 1, 0);
    return last < startOfDay(minD) || first > startOfDay(maxD);
  };

  const isYearDisabled = (year) => {
    const first = new Date(year, 0, 1);
    const last = new Date(year, 11, 31);
    return last < startOfDay(minD) || first > startOfDay(maxD);
  };

  const dateInputStr = toIsoDate(selected);

  const applyDateInput = (raw) => {
    const p = parseIsoDate(raw);
    if (!p) return;
    if (startOfDay(p) < startOfDay(minD) || startOfDay(p) > startOfDay(maxD)) return;
    setSelected(p);
    setMonthCursor(new Date(p.getFullYear(), p.getMonth(), 1));
  };

  const applyTimeInput = (raw) => {
    const t = String(raw || '').trim();
    if (!/^\d{1,2}:\d{2}$/.test(t)) return;
    const [hh, mm] = t.split(':').map((x) => parseInt(x, 10));
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return;
    setTimeStr(`${pad2(hh)}:${pad2(mm)}`);
  };

  const handleConfirm = () => {
    onConfirm(toIsoDate(selected));
  };

  const goPrevMonth = () => setMonthCursor(new Date(y, m - 1, 1));
  const goNextMonth = () => setMonthCursor(new Date(y, m + 1, 1));
  const goPrevYearNav = () => setMonthCursor(new Date(y - 1, m, 1));
  const goNextYearNav = () => setMonthCursor(new Date(y + 1, m, 1));
  const goPrevDecade = () => setDecade((d) => d - 20);
  const goNextDecade = () => setDecade((d) => d + 20);

  if (!open) return null;

  const years20 = Array.from({ length: 20 }, (_, i) => decade + i);

  return (
    <div
      className="dt-modal-overlay"
      ref={overlayRef}
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        className="dt-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dt-modal-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="dt-modal-header">
          <h2 id="dt-modal-title" className="dt-modal-title">
            Выбрать дату
          </h2>
          <button type="button" className="dt-modal-close" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="dt-modal-inputs">
          <label className="dt-modal-input-wrap">
            <input
              type="text"
              className="dt-modal-input"
              value={dateInputStr}
              onChange={(e) => applyDateInput(e.target.value)}
              disabled={disabled}
              inputMode="numeric"
              placeholder="ГГГГ-ММ-ДД"
            />
            <span className="dt-modal-input-icon" aria-hidden>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="5" width="18" height="16" rx="2" />
                <path d="M3 10h18M8 3v4M16 3v4" />
              </svg>
            </span>
          </label>
          <label className="dt-modal-input-wrap">
            <input
              type="text"
              className="dt-modal-input"
              value={timeStr}
              onChange={(e) => applyTimeInput(e.target.value)}
              disabled={disabled}
              placeholder="ЧЧ:ММ"
            />
            <span className="dt-modal-input-icon" aria-hidden>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v6l4 2" />
              </svg>
            </span>
          </label>
        </div>

        {view === 'days' ? (
          <>
            <div className="dt-modal-nav">
              <button type="button" className="dt-modal-nav-btn" onClick={goPrevMonth} disabled={disabled}>
                ‹
              </button>
              <button
                type="button"
                className="dt-modal-nav-title"
                onClick={() => setView('months')}
                disabled={disabled}
              >
                {MONTH_NAMES_FULL[m]} {y}
              </button>
              <button type="button" className="dt-modal-nav-btn" onClick={goNextMonth} disabled={disabled}>
                ›
              </button>
            </div>
            <div className="dt-modal-weekdays">
              {WEEKDAYS.map((w) => (
                <span key={w} className="dt-modal-weekday">
                  {w}
                </span>
              ))}
            </div>
            <div className="dt-modal-grid dt-modal-grid--days">
              {dayCells.map((cell, idx) =>
                cell.type === 'pad' ? (
                  <span key={`p-${idx}`} className="dt-modal-day dt-modal-day--pad" />
                ) : (
                  <button
                    key={cell.day}
                    type="button"
                    className={
                      'dt-modal-day' +
                      (selected.getDate() === cell.day &&
                      selected.getMonth() === m &&
                      selected.getFullYear() === y
                        ? ' dt-modal-day--selected'
                        : '') +
                      (isDayDisabled(cell.day) ? ' dt-modal-day--disabled' : '')
                    }
                    disabled={disabled || isDayDisabled(cell.day)}
                    onClick={() => setSelected(new Date(y, m, cell.day))}
                  >
                    {cell.day}
                  </button>
                ),
              )}
            </div>
          </>
        ) : null}

        {view === 'months' ? (
          <>
            <div className="dt-modal-nav">
              <button type="button" className="dt-modal-nav-btn" onClick={goPrevYearNav} disabled={disabled}>
                ‹
              </button>
              <button
                type="button"
                className="dt-modal-nav-title"
                onClick={() => {
                  setDecade(decadeStartYear(y));
                  setView('years');
                }}
                disabled={disabled}
              >
                {y}
              </button>
              <button type="button" className="dt-modal-nav-btn" onClick={goNextYearNav} disabled={disabled}>
                ›
              </button>
            </div>
            <div className="dt-modal-section-label">Месяцы</div>
            <div className="dt-modal-grid dt-modal-grid--months">
              {MONTH_SHORT.map((label, mi) => (
                <button
                  key={label}
                  type="button"
                  className={
                    'dt-modal-month' +
                    (m === mi ? ' dt-modal-month--selected' : '') +
                    (isMonthDisabled(mi) ? ' dt-modal-month--disabled' : '')
                  }
                  disabled={disabled || isMonthDisabled(mi)}
                  onClick={() => {
                    setMonthCursor(new Date(y, mi, 1));
                    setView('days');
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </>
        ) : null}

        {view === 'years' ? (
          <>
            <div className="dt-modal-nav">
              <button type="button" className="dt-modal-nav-btn" onClick={goPrevDecade} disabled={disabled}>
                ‹
              </button>
              <span className="dt-modal-nav-title dt-modal-nav-title--static">
                {decade} — {decade + 19}
              </span>
              <button type="button" className="dt-modal-nav-btn" onClick={goNextDecade} disabled={disabled}>
                ›
              </button>
            </div>
            <div className="dt-modal-section-label">Годы</div>
            <div className="dt-modal-grid dt-modal-grid--years">
              {years20.map((yr) => (
                <button
                  key={yr}
                  type="button"
                  className={
                    'dt-modal-year' +
                    (y === yr ? ' dt-modal-year--selected' : '') +
                    (isYearDisabled(yr) ? ' dt-modal-year--disabled' : '')
                  }
                  disabled={disabled || isYearDisabled(yr)}
                  onClick={() => {
                    setMonthCursor(new Date(yr, m, 1));
                    setDecade(decadeStartYear(yr));
                    setView('months');
                  }}
                >
                  {yr}
                </button>
              ))}
            </div>
          </>
        ) : null}

        <div className="dt-modal-footer">
          <button type="button" className="dt-modal-btn dt-modal-btn--ghost" onClick={onClose}>
            Отмена
          </button>
          <button type="button" className="dt-modal-btn dt-modal-btn--primary" onClick={handleConfirm} disabled={disabled}>
            Выбрать
          </button>
        </div>
      </div>
    </div>
  );
}
