export default function SkeletonCard() {
  return (
    <div className="bg-white rounded-md border border-border p-3.5 animate-pulse opacity-50">
      <div className="w-3/5 h-2 bg-border rounded mb-2" />
      <div className="w-4/5 h-1.5 bg-highlight rounded mb-1.5" />
      <div className="w-2/5 h-3 bg-border rounded" />
    </div>
  );
}
