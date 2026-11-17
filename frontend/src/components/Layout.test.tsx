import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthGate } from './AuthGate';
import { Layout } from './Layout';

afterEach(() => vi.restoreAllMocks());

function renderLayout() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
    String(input) === '/api/auth/status'
      ? new Response(JSON.stringify({ setup_required: false, authenticated: true, username: 'admin' }))
      : new Response(JSON.stringify({ detail: 'not in this test' }), { status: 404 }),
  );
  return render(
    <MemoryRouter>
      <AuthGate>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<p>page</p>} />
          </Route>
        </Routes>
      </AuthGate>
    </MemoryRouter>,
  );
}

// jsdom has no layout, so these guard the classes; the scrolling itself is checked in a browser.
describe('Layout header on a phone', () => {
  it('stays at the top while the page scrolls, and is in flow on desktop', async () => {
    renderLayout();
    const header = (await screen.findByRole('banner')) as HTMLElement;
    const cls = header.className.split(' ');
    expect(cls).toEqual(expect.arrayContaining(['sticky', 'top-0', 'z-30', 'lg:static']));
    expect(cls).toContain('bg-slate-900'); // opaque: page content must not show through
    expect(cls).not.toContain('bg-slate-900/60');
    expect(cls).toContain('print:hidden');
  });

  it('keeps the open menu inside the screen, scrolling on its own when it is too tall', async () => {
    renderLayout();
    fireEvent.click(await screen.findByRole('button', { name: 'Open menu' }));
    const menu = document.getElementById('mobile-menu')!;
    // A panel over the page (not in flow): opening it must not resize the header and shift the page.
    expect(menu.className).toEqual(expect.stringContaining('absolute inset-x-0 top-full'));
    expect(menu.className).toContain('bg-slate-900');
    expect(menu.className).toContain('max-h-[calc(100dvh-4rem)]');
    expect(menu.className).toContain('overflow-y-auto');
    expect(menu.className).toContain('overscroll-contain');
    expect(menu.closest('header')).not.toBeNull(); // the menu travels with the stuck header
  });
});
