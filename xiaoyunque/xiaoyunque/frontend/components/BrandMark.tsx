export default function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <rect width="40" height="40" rx="11" fill="#1D2441" />
      <rect x="8" y="11" width="15" height="18" rx="3" stroke="#F8F7F4" strokeWidth="2.3" />
      <path d="M18 11L31 18.5L18 29V11Z" fill="#F5AE77" />
      <circle cx="30" cy="10" r="2.3" fill="#8DD7CE" />
    </svg>
  );
}
