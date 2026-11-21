import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyDeviceSettings } from './CopyDeviceSettings';

afterEach(() => vi.restoreAllMocks());

const dev = (id: number, label: string, merged_into: number | null = null) => ({ id, label, merged_into });
const devices = [dev(1, 'Tablet'), dev(2, 'Kid phone'), dev(3, 'Spare TV'), dev(4, 'Old duplicate', 2)];

function mock(preview: object = { preview: true, destinations: [{ device_id: 1, label: 'Tablet', applied: ['2 always-allowed domains added'], skipped: [] }] }, fail = false) {
  const calls: { url: string; body?: { preview?: boolean; mode?: string; items?: string[]; also_to?: number[]; from_device_id?: number } }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url.startsWith('/api/devices')) return json({ now_ms: 1, devices });
    if (url.endsWith('/copy')) {
      const body = JSON.parse(String(init?.body));
      calls.push({ url, body });
      if (fail) return json({ detail: 'at most 50 always-allowed domains each' }, 422);
      return json(body.preview ? preview : { preview: false, destinations: [] });
    }
    return json({});
  });
  return calls;
}

const openIt = async (onDone = async () => undefined) => {
  render(<CopyDeviceSettings d={{ id: 1, label: 'Tablet' }} onDone={onDone} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy from another device' }));
  return screen.findByRole('dialog');
};

describe('CopyDeviceSettings', () => {
  it('offers the other devices only, never itself or a merged duplicate', async () => {
    mock();
    const dlg = await openIt();
    const pick = within(dlg).getByLabelText('Copy from') as HTMLSelectElement;
    await waitFor(() => expect(pick.options.length).toBe(3));
    expect([...pick.options].map((o) => o.text)).toEqual(['Choose a device…', 'Kid phone', 'Spare TV']);
  });

  it('previews, defaults to merge with all four items ticked, and copies after confirming', async () => {
    const calls = mock();
    const done = vi.fn(async () => undefined);
    const dlg = await openIt(done);
    await waitFor(() => expect((within(dlg).getByLabelText('Copy from') as HTMLSelectElement).options.length).toBe(3));
    expect(within(dlg).getByRole('button', { name: 'Copy…' })).toBeDisabled();
    fireEvent.change(within(dlg).getByLabelText('Copy from'), { target: { value: '2' } });
    expect(await within(dlg).findByText('2 always-allowed domains added')).toBeInTheDocument();
    expect(calls[0]!.body).toMatchObject({ from_device_id: 2, mode: 'merge', preview: true, also_to: [] });
    expect(calls[0]!.body!.items).toEqual(['allow', 'apps', 'filtering', 'schedules']);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Copy…' }));
    const confirm = (await screen.findAllByRole('dialog')).at(-1)!;
    expect(within(confirm).getByText("Copy Kid phone's settings to Tablet?")).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Copy settings' }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(calls.at(-1)!.body!.preview).toBeUndefined();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('asks again with Replace and more devices, and cannot copy with nothing ticked', async () => {
    const calls = mock();
    const dlg = await openIt();
    await waitFor(() => expect((within(dlg).getByLabelText('Copy from') as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(within(dlg).getByLabelText('Copy from'), { target: { value: '2' } });
    fireEvent.click(within(dlg).getByLabelText(/Replace/));
    fireEvent.click(within(dlg).getByText(/Also copy to other devices/));
    fireEvent.click(within(dlg).getByLabelText('Spare TV'));
    await waitFor(() => expect(calls.at(-1)!.body).toMatchObject({ mode: 'replace', also_to: [3] }));
    for (const name of ['Always-allowed domains', 'Blocked apps', 'Filtering and YouTube', 'Internet-off schedules']) {
      fireEvent.click(within(dlg).getByLabelText(name));
    }
    expect(within(dlg).getByRole('button', { name: 'Copy…' })).toBeDisabled();
  });

  it('shows the reason when the preview is refused', async () => {
    mock({}, true);
    const dlg = await openIt();
    await waitFor(() => expect((within(dlg).getByLabelText('Copy from') as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(within(dlg).getByLabelText('Copy from'), { target: { value: '2' } });
    expect(await within(dlg).findByRole('alert')).toHaveTextContent('at most 50 always-allowed domains each');
    expect(within(dlg).getByRole('button', { name: 'Copy…' })).toBeDisabled();
  });
});
