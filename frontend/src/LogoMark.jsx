import { useId } from 'react';

export function LogoMark({ className, 'aria-hidden': ariaHidden = true }) {
  const uid = useId().replace(/:/g, '');
  const filterId = `logo-shadow-${uid}`;

  return (
    <svg
      className={className}
      width="640"
      height="420"
      viewBox="0 0 640 420"
      aria-hidden={ariaHidden}
      focusable="false"
    >
      <defs>
        <filter id={filterId} x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="6" stdDeviation="8" floodOpacity="0.15" />
        </filter>
      </defs>
      <g filter={`url(#${filterId})`}>
        <path
          d="M198 80 H442 Q470 80 470 108 V242 Q470 270 442 270 H322 L250 340 L278 270 H198 Q170 270 170 242 V108 Q170 80 198 80Z"
          fill="#ffffff"
          stroke="#111827"
          strokeWidth="8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </g>
      <g>
        <line x1="250" y1="110" x2="250" y2="240" stroke="#111827" strokeWidth="12" strokeLinecap="round" />
        <rect x="225" y="160" width="50" height="60" rx="16" fill="#ffffff" stroke="#111827" strokeWidth="8" />
      </g>
      <g>
        <line x1="320" y1="100" x2="320" y2="245" stroke="#111827" strokeWidth="12" strokeLinecap="round" />
        <rect x="295" y="160" width="50" height="50" rx="16" fill="#111827" stroke="#111827" strokeWidth="8" />
      </g>
      <g>
        <line x1="390" y1="90" x2="390" y2="250" stroke="#111827" strokeWidth="12" strokeLinecap="round" />
        <rect x="365" y="140" width="50" height="70" rx="16" fill="#ffffff" stroke="#111827" strokeWidth="8" />
      </g>
    </svg>
  );
}
