import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EMPTY_TARGET, TargetForm } from './TargetForm';

describe('TargetForm', () => {
  it('switches fields by probe kind and submits', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<TargetForm initial={EMPTY_TARGET} submitLabel="Add" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'NAS SSH' } });
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'tcp' } });
    expect(screen.getByLabelText('Port')).toBeInTheDocument();
    expect(screen.getByLabelText('Interval (s)')).toHaveValue(30);
    fireEvent.change(screen.getByLabelText('Host or IP'), { target: { value: '192.168.2.15' } });
    fireEvent.change(screen.getByLabelText('Port'), { target: { value: '22' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ name: 'NAS SSH', kind: 'tcp', host: '192.168.2.15', port: 22, interval_s: 30 });
  });

  it('shows server errors', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('host: must be an IP address or a valid hostname'));
    render(<TargetForm initial={{ ...EMPTY_TARGET, name: 'x', host: 'bad host' }} submitLabel="Add" onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('valid hostname');
  });
});
