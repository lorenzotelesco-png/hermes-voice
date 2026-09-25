import { useEffect, useState } from 'preact/hooks';
import { chat, type Approval } from './store';

/**
 * Hermes wants to run something that needs a yes. Shown over every tab: a
 * turn left waiting here is blocked, and Hermes refuses the command by itself
 * once its approval timeout runs out.
 */
export function ApprovalSheet() {
  const [approval, setApproval] = useState<Approval | null>(chat.snapshot.approval);
  useEffect(() => chat.subscribe(s => setApproval(s.approval)), []);
  if (!approval) return null;

  return (
    <div class="sheet-backdrop">
      <div class="sheet" role="alertdialog" aria-labelledby="sheet-title" aria-describedby="sheet-cmd">
        <h3 id="sheet-title">Hermes vuole eseguire</h3>
        <pre id="sheet-cmd" class="cmd">{approval.command || '(comando non disponibile)'}</pre>
        {approval.description && <p class="why">{approval.description}</p>}
        <div class="sheet-actions">
          <button class="btn btn-deny" onClick={() => chat.answer('deny')}>Nega</button>
          <button class="btn btn-ok" onClick={() => chat.answer('once')}>Approva</button>
        </div>
        {/* Hermes scopes "session" to the run on this endpoint, so it covers
            the rest of this reply, not the whole conversation. */}
        {approval.choices.includes('session') && (
          <button class="btn-link" onClick={() => chat.answer('session')}>
            Approva anche quelli simili fino alla fine della risposta
          </button>
        )}
      </div>
    </div>
  );
}
