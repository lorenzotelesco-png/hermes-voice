// A tab whose phase has not landed yet: says what will be here and when.
export function SoonTab({ title, phase, children }: { title: string; phase: number; children: string }) {
  return (
    <section class="page soon">
      <h2>{title}</h2>
      <p>{children}</p>
      <span class="badge">fase {phase}</span>
    </section>
  );
}
