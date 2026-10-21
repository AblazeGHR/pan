// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { PluginSelection } from './PluginSelection';
import type { McpServerInfo, PluginBinding } from '@/types';

const servers: McpServerInfo[] = [
  { name: 'pan', supportedModes: ['standalone'] },
  { name: 'qq', aliases: ['pan-qq'], supportedModes: ['standalone', 'pan_native', 'gateway'] },
];

function Harness() {
  const [bindings, setBindings] = useState<PluginBinding[]>([
    { plugin_id: 'qq', enabled: true, mode: 'standalone' },
  ]);
  return <><PluginSelection servers={servers} bindings={bindings} onChange={(binding) =>
    setBindings((current) => [...current.filter((b) => b.plugin_id !== binding.plugin_id), binding])} />
    <output data-testid="bindings">{JSON.stringify(bindings)}</output></>;
}

afterEach(cleanup);

describe('PluginSelection', () => {
  it('keeps the mode after disabling and re-enabling a plugin', () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('qq mode'), { target: { value: 'pan_native' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'qq' }));
    expect(JSON.parse(screen.getByTestId('bindings').textContent!)).toEqual([
      { plugin_id: 'qq', enabled: false, mode: 'pan_native' },
    ]);
    fireEvent.click(screen.getByRole('checkbox', { name: 'qq' }));
    expect((screen.getByLabelText('qq mode') as HTMLSelectElement).value).toBe('pan_native');
    expect((screen.getByRole('checkbox', { name: 'pan' }) as HTMLInputElement).checked).toBe(false);
  });

  it('does not let the mode selector toggle the row checkbox', () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('qq mode'), { target: { value: 'gateway' } });
    expect((screen.getByRole('checkbox', { name: 'qq' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText('pan mode') as HTMLSelectElement).disabled).toBe(true);
  });

  it('preserves the last-enabled template lock and shows unsupported modes', () => {
    render(<PluginSelection servers={servers} bindings={[{ plugin_id: 'qq', enabled: true, mode: 'gateway' }]}
      keepOne onChange={() => { throw new Error('locked checkbox changed'); }} />);
    expect((screen.getByRole('checkbox', { name: 'qq' }) as HTMLInputElement).disabled).toBe(true);
    const pan = screen.getByLabelText('pan mode') as HTMLSelectElement;
    expect([...pan.options].filter((o) => o.disabled).map((o) => o.value)).toEqual(['pan_native', 'gateway']);
  });
});
