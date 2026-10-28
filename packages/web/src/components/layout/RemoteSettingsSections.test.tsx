// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { LanSettings } from './LanSettings';
import { TailscaleSettings } from './TailscaleSettings';
import { McpRemoteSettings } from './McpRemoteSettings';

type RecordedCall = { url: string; method: string; body: unknown };

type LanStatus = {
  config: { startupEnabled: boolean; host: string; port: number } | null;
  activeConfig: { startupEnabled: boolean; host: string; port: number } | null;
  running: boolean;
  addresses: string[];
  urls: string[];
  error: string;
};

type TailscaleStatus = {
  config: { startupEnabled: boolean; protocol: 'http' | 'https'; port: number };
  running: boolean;
  online?: boolean;
  url: string;
  ip?: string;
  dns?: string;
  target: string;
  error: string;
  pendingCleanup: boolean;
};

type McpStatus = {
  config: {
    enabled: boolean;
    port: number;
    public_hostname: string;
    access_issuer: string;
    access_audience: string;
    config_path: string;
    binary_path: string;
  };
  publicUrl: string;
  gateway: { listening: boolean };
  tunnel: { running: boolean };
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function lanStatus(overrides: Partial<LanStatus> = {}): LanStatus {
  return {
    config: { startupEnabled: false, host: '', port: 8780 },
    activeConfig: null,
    running: false,
    addresses: ['192.168.1.10'],
    urls: [],
    error: '',
    ...overrides,
  };
}

function tailscaleStatus(overrides: Partial<TailscaleStatus> = {}): TailscaleStatus {
  return {
    config: { startupEnabled: false, protocol: 'http', port: 18080 },
    running: false,
    online: false,
    url: '',
    ip: '',
    dns: '',
    target: '',
    error: '',
    pendingCleanup: false,
    ...overrides,
  };
}

function mcpStatus(overrides: Partial<McpStatus> = {}): McpStatus {
  return {
    config: {
      enabled: false,
      port: 8769,
      public_hostname: 'mcp.example.com',
      access_issuer: 'https://team.cloudflareaccess.com',
      access_audience: 'aud-tag',
      config_path: 'C:\\fixture\\mcp-tunnel.yml',
      binary_path: '',
    },
    publicUrl: 'https://mcp.example.com/mcp',
    gateway: { listening: true },
    tunnel: { running: true },
    ...overrides,
  };
}

let calls: RecordedCall[] = [];
let lan: LanStatus;
let tailscale: TailscaleStatus;
let mcp: McpStatus;
let mcpFailure: string | null;

beforeEach(() => {
  calls = [];
  lan = lanStatus();
  tailscale = tailscaleStatus();
  mcp = mcpStatus();
  mcpFailure = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url === '/api/remote/lan') {
        if (method === 'PUT') {
          lan = { ...lan, config: body };
          return json(lan);
        }
        return json(lan);
      }
      if (url === '/api/remote/lan/start') {
        lan = { ...lan, running: true, activeConfig: lan.config };
        return json(lan);
      }
      if (url === '/api/remote/lan/stop') {
        lan = { ...lan, running: false, activeConfig: null };
        return json(lan);
      }
      if (url === '/api/remote/tailscale') {
        if (method === 'PUT') {
          tailscale = { ...tailscale, config: body };
          return json(tailscale);
        }
        return json(tailscale);
      }
      if (url === '/api/remote/tailscale/start') {
        tailscale = { ...tailscale, running: true, online: true, url: 'http://100.64.0.9:18080' };
        return json(tailscale);
      }
      if (url === '/api/remote/tailscale/stop') {
        tailscale = { ...tailscale, running: false };
        return json(tailscale);
      }
      if (url === '/api/remote/mcp') {
        if (mcpFailure) return json({ detail: mcpFailure }, 500);
        return json(mcp);
      }
      if (url === '/api/remote/mcp/start' || url === '/api/remote/mcp/restart') {
        return json({ ...mcp, ok: true });
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

function section(testId: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  expect(element).toBeTruthy();
  return element!;
}

function toggle(sectionElement: HTMLElement) {
  return within(sectionElement).getAllByRole('button')[0]!;
}

function body(sectionElement: HTMLElement): HTMLElement {
  const id = toggle(sectionElement).getAttribute('aria-controls');
  const element = document.getElementById(id!);
  expect(element).toBeTruthy();
  return element!;
}

describe('Remote settings sections', () => {
  it('renders each capability as a titled, collapsible section with a status summary', async () => {
    render(
      <>
        <McpRemoteSettings />
        <TailscaleSettings />
        <LanSettings />
      </>,
    );

    await waitFor(() => {
      expect(section('remote-mcp-section').textContent).toContain('网关：就绪 · 隧道进程：运行中');
      expect(section('remote-tailscale-section').textContent).toContain('已关闭 · Tailscale 未就绪');
      expect(section('remote-lan-section').textContent).toContain('已关闭');
    });

    for (const [testId, title] of [
      ['remote-mcp-section', 'MCP 远程连接'],
      ['remote-tailscale-section', 'Tailscale 备用访问'],
      ['remote-lan-section', '普通局域网访问（LAN）'],
    ] as const) {
      const element = section(testId);
      const header = toggle(element);
      expect(element.querySelector('h3')?.textContent).toContain(title);
      expect(header.tagName).toBe('BUTTON');
      expect(header.getAttribute('aria-expanded')).toBe('true');
      expect(body(element).hidden).toBe(false);
    }

    const lanSection = section('remote-lan-section');
    fireEvent.click(toggle(lanSection));
    expect(toggle(lanSection).getAttribute('aria-expanded')).toBe('false');
    expect(body(lanSection).hidden).toBe(true);
    expect(lanSection.textContent).toContain('已关闭');
    fireEvent.click(toggle(lanSection));
    expect(body(lanSection).hidden).toBe(false);
  });

  it('keeps unsaved edits and never refetches when a section is collapsed and reopened', async () => {
    render(
      <>
        <McpRemoteSettings />
        <LanSettings />
      </>,
    );

    const hostname = (await waitFor(() => {
      const input = document.getElementById('mcp-public_hostname') as HTMLInputElement | null;
      expect(input).toBeTruthy();
      return input!;
    }));
    fireEvent.change(hostname, { target: { value: 'edited.example.com' } });
    const lanPort = document.getElementById('lan-port') as HTMLInputElement;
    fireEvent.change(lanPort, { target: { value: '8899' } });

    const mcpCalls = calls.filter((call) => call.url === '/api/remote/mcp').length;
    const lanCalls = calls.filter((call) => call.url === '/api/remote/lan').length;
    const mcpSection = section('remote-mcp-section');
    const lanSection = section('remote-lan-section');

    fireEvent.click(toggle(mcpSection));
    fireEvent.click(toggle(lanSection));
    expect(body(mcpSection).hidden).toBe(true);
    expect(body(lanSection).hidden).toBe(true);
    expect(calls.filter((call) => call.url === '/api/remote/mcp').length).toBe(mcpCalls);
    expect(calls.filter((call) => call.url === '/api/remote/lan').length).toBe(lanCalls);

    fireEvent.click(toggle(mcpSection));
    fireEvent.click(toggle(lanSection));
    expect((document.getElementById('mcp-public_hostname') as HTMLInputElement).value).toBe(
      'edited.example.com',
    );
    expect((document.getElementById('lan-port') as HTMLInputElement).value).toBe('8899');
    expect(calls.filter((call) => call.url === '/api/remote/mcp').length).toBe(mcpCalls);
    expect(calls.filter((call) => call.url === '/api/remote/lan').length).toBe(lanCalls);
  });

  it('exposes native buttons with aria-expanded/aria-controls for keyboard toggling', async () => {
    render(<TailscaleSettings />);
    await waitFor(() =>
      expect(document.getElementById('tailscale-port')).toBeTruthy(),
    );
    const header = toggle(section('remote-tailscale-section'));
    expect(header.getAttribute('type')).toBe('button');
    expect(header.getAttribute('tabindex')).not.toBe('-1');
    expect(header.getAttribute('aria-expanded')).toBe('true');
    expect(header.getAttribute('aria-controls')).toBe(body(section('remote-tailscale-section')).id);
    header.focus();
    expect(document.activeElement).toBe(header);
  });

  it('keeps error and unavailable states visible inside their section', async () => {
    mcpFailure = 'MCP 配置读取失败';
    render(
      <>
        <McpRemoteSettings />
        <LanSettings />
      </>,
    );

    const alert = await waitFor(() => {
      const element = within(section('remote-mcp-section')).getByRole('alert');
      expect(element.textContent).toBe('MCP 配置读取失败');
      return element;
    });
    expect(alert).toBeTruthy();
    expect(section('remote-mcp-section').textContent).toContain('状态不可用');

    lan = lanStatus({ error: 'LAN 端口已被占用' });
    cleanup();
    render(<LanSettings />);
    await waitFor(() =>
      expect(within(section('remote-lan-section')).getByRole('alert').textContent).toBe(
        'LAN 端口已被占用',
      ),
    );
  });

  it('keeps save/start/stop requests on their original endpoints and payloads', async () => {
    render(
      <>
        <LanSettings />
        <TailscaleSettings />
        <McpRemoteSettings />
      </>,
    );

    const lanHost = (await waitFor(() => {
      const element = document.getElementById('lan-host') as HTMLSelectElement | null;
      expect(element).toBeTruthy();
      return element!;
    }));
    fireEvent.change(lanHost, { target: { value: '192.168.1.10' } });
    fireEvent.change(document.getElementById('lan-port')!, { target: { value: '8899' } });
    fireEvent.click(
      within(section('remote-lan-section')).getByRole('button', { name: '保存 LAN 配置' }),
    );
    expect(calls).toContainEqual({
      url: '/api/remote/lan',
      method: 'PUT',
      body: { startupEnabled: false, host: '192.168.1.10', port: 8899 },
    });
    await waitFor(() =>
      expect(
        (within(section('remote-lan-section')).getByRole('button', {
          name: '开启 LAN 访问',
        }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(
      within(section('remote-lan-section')).getByRole('button', { name: '开启 LAN 访问' }),
    );
    expect(calls.some((call) => call.url === '/api/remote/lan/start' && call.method === 'POST')).toBe(
      true,
    );

    fireEvent.change(document.getElementById('tailscale-port')!, { target: { value: '18081' } });
    fireEvent.click(
      within(section('remote-tailscale-section')).getByRole('button', { name: '保存配置' }),
    );
    expect(calls).toContainEqual({
      url: '/api/remote/tailscale',
      method: 'PUT',
      body: { startupEnabled: false, protocol: 'http', port: 18081 },
    });
    await waitFor(() =>
      expect(
        (within(section('remote-tailscale-section')).getByRole('button', {
          name: '开启当前访问',
        }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(
      within(section('remote-tailscale-section')).getByRole('button', { name: '开启当前访问' }),
    );
    expect(
      calls.some((call) => call.url === '/api/remote/tailscale/start' && call.method === 'POST'),
    ).toBe(true);

    fireEvent.click(within(section('remote-mcp-section')).getByRole('button', { name: '启动' }));
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/remote/mcp/start' && call.method === 'POST')).toBe(
        true,
      ),
    );
  });
});
