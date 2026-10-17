import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { UNAUTHORIZED_EVENT } from '../lib/api';
import { AuthGate } from './AuthGate';

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('AuthGate', () => {
  it('shows first-run setup and enters the app after creating the account', async () => {
    let created = false;
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/auth/status') {
        return json(created ? { setup_required: false, authenticated: true, username: 'admin' } : { setup_required: true, authenticated: false, username: null });
      }
      if (url === '/api/auth/setup') {
        expect((init?.headers as Record<string, string>)['X-Requested-With']).toBe('netwatch');
        created = true;
        return json({ username: 'admin' }, 201);
      }
      return json({}, 404);
    });
    render(<AuthGate><p>secret app</p></AuthGate>);
    expect(await screen.findByText('Welcome to NetWatch')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-pw' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'different-pw-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'long-enough-pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('secret app')).toBeInTheDocument();
    expect(f).toHaveBeenCalledWith('/api/auth/setup', expect.anything());
  });

  it('shows login errors and returns to login on a 401 event', async () => {
    let authed = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/auth/status') return json({ setup_required: false, authenticated: authed, username: authed ? 'admin' : null });
      if (url === '/api/auth/login') {
        const body = JSON.parse(String(init?.body));
        if (body.password !== 'right-password') return json({ detail: 'invalid username or password' }, 401);
        authed = true;
        return json({ username: 'admin' });
      }
      return json({}, 404);
    });
    render(<AuthGate><p>secret app</p></AuthGate>);
    expect(await screen.findByText('Sign in to NetWatch')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid username or password');
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'right-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('secret app')).toBeInTheDocument();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    await waitFor(() => expect(screen.getByText('Sign in to NetWatch')).toBeInTheDocument());
  });
});
