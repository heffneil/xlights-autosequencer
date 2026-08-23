import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SingerPartsDialog } from '../../src/components/SingerPartsDialog/SingerPartsDialog';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

/** Health probe fires on mount; default it to "aligner up". */
function healthOk() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ available: true, url: 'http://localhost:3001' }),
  });
}

function fill(index: number, name: string, lyrics: string) {
  fireEvent.change(screen.getByTestId(`singer-name-${index}`), { target: { value: name } });
  fireEvent.change(screen.getByTestId(`singer-lyrics-${index}`), { target: { value: lyrics } });
}

describe('SingerPartsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('defaults to three singer boxes and follows the count input', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getAllByTestId(/^singer-part-/)).toHaveLength(3);

    fireEvent.change(screen.getByTestId('singer-count'), { target: { value: '5' } });
    expect(screen.getAllByTestId(/^singer-part-/)).toHaveLength(5);
  });

  it('keeps what was already typed when the count grows', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    fill(0, 'JC', 'Noel');
    fireEvent.change(screen.getByTestId('singer-count'), { target: { value: '4' } });
    expect(screen.getByTestId('singer-name-0')).toHaveValue('JC');
    expect(screen.getByTestId('singer-lyrics-0')).toHaveValue('Noel');
  });

  it('POSTs only the filled parts and calls onSaved', async () => {
    healthOk();
    const onSaved = vi.fn();
    render(<SingerPartsDialog songId="song42" onSaved={onSaved} onCancel={() => {}} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));

    fill(0, 'JC', 'Noel shepherds');
    fill(1, 'Justin', 'Noel star');
    // third box deliberately left blank

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        found: true, word_count: 3, phoneme_count: 9, shared_word_count: 1,
        singers: [{ name: 'JC', word_count: 2 }, { name: 'Justin', word_count: 2 }],
        tracks: ['Lyrics - JC', 'Lyrics - Justin'],
      }),
    });
    fireEvent.click(screen.getByTestId('singer-align'));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const [url, opts] = mockFetch.mock.calls[1];
    expect(url).toBe('/api/v1/songs/song42/lyrics/singers');
    const body = JSON.parse(opts.body);
    expect(body.parts).toHaveLength(2);
    expect(body.parts.map((p: { name: string }) => p.name)).toEqual(['JC', 'Justin']);
  });

  it('rejects duplicate singer names without POSTing', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    fill(0, 'JC', 'a');
    fill(1, 'jc', 'b');
    fireEvent.click(screen.getByTestId('singer-align'));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/unique/i);
    });
    expect(mockFetch).toHaveBeenCalledTimes(1); // health only
  });

  it('disables aligning until a singer has both a name and lyrics', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByTestId('singer-align')).toBeDisabled();
    fill(0, 'JC', 'Noel');
    expect(screen.getByTestId('singer-align')).toBeEnabled();
  });

  it('warns and blocks when the aligner service is down', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ available: false, url: 'http://localhost:3001' }),
    });
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/isn.t\s+responding/i);
    });
    fill(0, 'JC', 'Noel');
    expect(screen.getByTestId('singer-align')).toBeDisabled();
  });

  it('surfaces a server error message', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    fill(0, 'JC', 'Noel');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: { code: 'aligner_busy', message: 'An alignment run is already in progress' } }),
    });
    fireEvent.click(screen.getByTestId('singer-align'));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/already in progress/i);
    });
  });

  it('shows progress while aligning', async () => {
    healthOk();
    render(<SingerPartsDialog songId="s1" onSaved={() => {}} onCancel={() => {}} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    fill(0, 'JC', 'Noel');
    let release: (v: unknown) => void = () => {};
    mockFetch.mockReturnValueOnce(new Promise((r) => { release = r; }));
    fireEvent.click(screen.getByTestId('singer-align'));
    await waitFor(() => {
      expect(screen.getByTestId('singer-progress')).toHaveTextContent(/about\s+a\s+minute/i);
    });
    release({ ok: true, json: async () => ({ found: true, singers: [] }) });
  });
});
