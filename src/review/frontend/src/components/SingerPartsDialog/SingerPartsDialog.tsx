import React, { useEffect, useState } from 'react';
import styles from './SingerPartsDialog.module.css';

export interface SingerPartsResult {
  found: boolean;
  word_count: number;
  phoneme_count: number;
  shared_word_count: number;
  singers: { name: string; word_count: number }[];
  tracks?: string[];
  warnings?: string[];
}

interface SingerPartsDialogProps {
  songId: string;
  onSaved: (result: SingerPartsResult) => void;
  onCancel: () => void;
}

interface Part {
  name: string;
  lyrics: string;
}

const MIN_SINGERS = 1;
const MAX_SINGERS = 12;

function blankParts(n: number, existing: Part[] = []): Part[] {
  return Array.from({ length: n }, (_, i) => existing[i] ?? { name: '', lyrics: '' });
}

/**
 * Per-singer lyric tracks: name each singer and paste only their lines.
 *
 * Each part is aligned separately against the mix by the AutoLyrixAlign
 * service, which — unlike forced alignment — leaves a singer's silent verses
 * empty instead of spreading their words across the whole song. Lines several
 * singers share (a full-cast chorus) come back with the same timing and land
 * on every one of their tracks.
 */
export function SingerPartsDialog({ songId, onSaved, onCancel }: SingerPartsDialogProps) {
  const [count, setCount] = useState(3);
  const [parts, setParts] = useState<Part[]>(() => blankParts(3));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [alignerUp, setAlignerUp] = useState<boolean | null>(null);
  const [alignerUrl, setAlignerUrl] = useState('');

  useEffect(() => {
    let alive = true;
    fetch('/api/v1/lyrics/aligner/health')
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return;
        setAlignerUp(Boolean(d.available));
        setAlignerUrl(String(d.url || ''));
      })
      .catch(() => alive && setAlignerUp(false));
    return () => {
      alive = false;
    };
  }, []);

  function changeCount(next: number) {
    const n = Math.max(MIN_SINGERS, Math.min(MAX_SINGERS, next));
    setCount(n);
    setParts((prev) => blankParts(n, prev));
  }

  function update(i: number, field: keyof Part, value: string) {
    setParts((prev) => prev.map((p, j) => (j === i ? { ...p, [field]: value } : p)));
  }

  async function handleAlign() {
    const filled = parts
      .map((p) => ({ name: p.name.trim(), lyrics: p.lyrics }))
      .filter((p) => p.name && p.lyrics.trim());
    if (!filled.length) {
      setError('Give at least one singer a name and some lyrics');
      return;
    }
    const names = filled.map((p) => p.name.toLowerCase());
    if (new Set(names).size !== names.length) {
      setError('Singer names must be unique');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/v1/songs/${songId}/lyrics/singers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parts: filled }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error?.message || 'Alignment failed');
      onSaved(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Alignment failed');
      setBusy(false);
    }
  }

  const filledCount = parts.filter((p) => p.name.trim() && p.lyrics.trim()).length;

  return (
    <div className={styles.overlay}>
      <div role="dialog" aria-modal="true" aria-label="Per-singer lyrics" className={styles.dialog}>
        <h2 className={styles.title}>Per-Singer Lyric Tracks</h2>
        <p className={styles.subtitle}>
          Name each singer and paste <em>only their lines</em> — leave out the parts
          someone else sings. Lines you all sing together can appear in every box;
          they&apos;ll be matched up and land on each singer&apos;s track.
        </p>

        {alignerUp === false && (
          <p role="alert" className={styles.error}>
            The alignment service at {alignerUrl || 'the configured URL'} isn&apos;t
            responding. Start the <code>xonset-aligner</code> container before continuing.
          </p>
        )}

        <label className={styles.countRow}>
          <span>Number of singers</span>
          <input
            type="number"
            min={MIN_SINGERS}
            max={MAX_SINGERS}
            value={count}
            className={styles.countInput}
            data-testid="singer-count"
            onChange={(e) => changeCount(Number(e.target.value) || MIN_SINGERS)}
            disabled={busy}
          />
        </label>

        <div className={styles.parts}>
          {parts.map((p, i) => (
            <div key={i} className={styles.part} data-testid={`singer-part-${i}`}>
              <input
                type="text"
                className={styles.nameInput}
                placeholder={`Singer ${i + 1} name`}
                aria-label={`Singer ${i + 1} name`}
                data-testid={`singer-name-${i}`}
                value={p.name}
                onChange={(e) => update(i, 'name', e.target.value)}
                disabled={busy}
              />
              <textarea
                className={styles.textarea}
                placeholder="Paste this singer's lines…"
                aria-label={`Singer ${i + 1} lyrics`}
                data-testid={`singer-lyrics-${i}`}
                value={p.lyrics}
                onChange={(e) => update(i, 'lyrics', e.target.value)}
                disabled={busy}
              />
            </div>
          ))}
        </div>

        {error && <p role="alert" className={styles.error}>{error}</p>}

        {busy && (
          <p className={styles.progress} data-testid="singer-progress">
            Aligning {filledCount} singer{filledCount === 1 ? '' : 's'}… this takes about
            a minute each and they run one at a time, so please leave this open.
          </p>
        )}

        <div className={styles.actions}>
          <button className={styles.btnCancel} data-testid="singer-cancel" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnConfirm}
            data-testid="singer-align"
            onClick={handleAlign}
            disabled={busy || alignerUp === false || filledCount === 0}
          >
            {busy ? 'Aligning…' : `Align ${filledCount || ''} Singer${filledCount === 1 ? '' : 's'}`.trim()}
          </button>
        </div>
      </div>
    </div>
  );
}
