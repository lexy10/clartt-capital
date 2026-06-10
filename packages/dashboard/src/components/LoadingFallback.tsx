export default function LoadingFallback() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        width: '100%',
        color: 'var(--text-muted)',
        fontFamily: 'var(--font-sans)',
        fontSize: '0.875rem',
        letterSpacing: '0.025em',
      }}
    >
      Loading…
    </div>
  );
}
