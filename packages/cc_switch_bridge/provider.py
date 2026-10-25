"""Capability helper. Never imports cc-switch or opens its database directly."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
from contextlib import contextmanager

APPS = {'codex', 'claude', 'gemini'}
SETTINGS_FILE = 'provider-flags.json'


class BridgeError(ValueError):
    """A bridge failure with a stable code the Panel can act on."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CliError(BridgeError):
    """A CLI failure with a stable code the Panel can act on.

    The Panel only offers installation guidance for the CLI-availability codes
    below; ordinary execution or quota failures must not be mistaken for a
    missing CLI.
    """


class ProviderSettings:
    """Per-app, per-provider gates owned by the bridge plugin only.

    Nothing here is written to the cc-switch CLI database or understood by the
    CLI; the file lives in the plugin data directory so the gate survives Pan
    restarts without changing the installed cc-switch.
    """

    def __init__(self, data_dir):
        self.path = Path(data_dir) / SETTINGS_FILE

    def read(self):
        if not self.path.exists():
            return {'version': 1, 'apps': {}}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise BridgeError('settings_invalid', '插件设置文件无法读取；请检查 provider-flags.json') from exc
        if not isinstance(value, dict) or not isinstance(value.get('apps', {}), dict):
            raise BridgeError('settings_invalid', '插件设置文件格式无效；请检查 provider-flags.json')
        return {'version': 1, 'apps': value.get('apps') or {}}

    def app(self, app):
        state = self.read()['apps'].get(app) or {}
        if not isinstance(state, dict):
            raise BridgeError('settings_invalid', '插件设置文件格式无效；请检查 provider-flags.json')
        revision = state.get('revision', 0)
        updated = state.get('updatedAt', 0)
        providers = state.get('providers') or {}
        if (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0
                or not isinstance(updated, int) or isinstance(updated, bool)
                or not isinstance(providers, dict)):
            raise BridgeError('settings_invalid', '插件设置文件格式无效；请检查 provider-flags.json')
        clean = {}
        for provider_id, entry in providers.items():
            if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 256:
                raise BridgeError('settings_invalid', '插件设置文件格式无效；请检查 provider-flags.json')
            disabled = (entry or {}).get('nonManualDisabled', False) if isinstance(entry, dict) else None
            if not isinstance(disabled, bool):
                raise BridgeError('settings_invalid', '插件设置文件格式无效；请检查 provider-flags.json')
            clean[provider_id] = {'nonManualDisabled': disabled}
        return {'revision': revision, 'updatedAt': updated, 'providers': clean}

    def gates(self, app):
        """Map provider id to its single non-manual gate."""
        return {provider_id: entry['nonManualDisabled']
                for provider_id, entry in self.app(app)['providers'].items()}

    def gated(self, app, provider_id):
        return self.gates(app).get(provider_id, False)

    def update(self, app, provider_id, disabled, expected_revision):
        """Replace one gate and return the new per-app revision.

        Callers hold the bridge lock, so two helpers cannot interleave the
        read-modify-write even though each one is a separate process.
        """
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError('无效的设置版本号')
        state = self.read()
        current = self.app(app)
        if current['revision'] != expected_revision:
            raise BridgeError('settings_conflict', '设置已被其他窗口修改；请刷新后再操作')
        providers = dict(state['apps'].get(app, {}).get('providers') or {})
        if len(providers) >= 512 and provider_id not in providers:
            raise ValueError('配置数量超出设置容量')
        providers[provider_id] = {'nonManualDisabled': bool(disabled)}
        state['apps'][app] = {'revision': current['revision'] + 1,
                              'updatedAt': int(time.time() * 1000), 'providers': providers}
        self._write(state)
        return self.app(app)['revision']

    def _write(self, value):
        # Same atomic pattern as the quota cache: a reader never observes a
        # partially written gate file.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


class Bridge:
    def __init__(self, data_dir, runner=None):
        self.data_dir = Path(data_dir)
        self.settings = ProviderSettings(self.data_dir)
        self.runner = runner or self._run

    def private_launch_supported(self):
        """Internal capability probe only; never list/read a live profile.

        Existing v1 CLIs do not advertise this extension and receive no export
        request. This method is deliberately absent from plugin capabilities.
        """
        self._deadline = time.monotonic() + 8
        version = self.runner(['--version'])
        return (isinstance(version, dict) and version.get('contract') == 'pan-cc-switch-gui/v1'
                and version.get('privateLaunchExport') == 'pan-cc-switch-launch/v1')

    def private_launch_export(self, request_path):
        path = Path(request_path)
        if not path.is_absolute():raise ValueError('Private launch request must be absolute')
        # Owner writes secret material only to the exclusive private request
        # directory. CLI stdout is a bounded non-secret receipt, not a config.
        result = self.runner(['--app', 'codex', 'provider', 'launch-export', str(path), '--json'])
        if not isinstance(result,dict) or len(json.dumps(result).encode('utf-8')) > 16*1024:
            raise ValueError('Invalid private launch receipt')
        return result

    def read_only(self, operation, arguments, *, deadline=None):
        """Bounded policy reads: no bridge lock/cache write or mutation command.

        Use a request-local CLI deadline; concurrent reads must not change another
        operation's budget. Existing invoke/Panel write contracts stay unchanged.
        """
        if arguments.get('app') != 'codex' or operation not in ('list', 'quota', 'managed'):
            raise ValueError('Unsupported read-only operation')
        expected = {'app', 'id'} if operation == 'quota' else {'app'}
        if set(arguments) != expected:raise ValueError('Unknown read-only arguments')
        local = Bridge(self.data_dir, None if self.runner == self._run else self.runner)
        local._deadline = min(time.monotonic()+8, deadline) if deadline is not None else time.monotonic()+8
        if time.monotonic() >= local._deadline:raise ValueError('Read budget exhausted')
        if operation == 'managed':
            raw = local.runner(['auth', 'status', '--json'])
            if (not isinstance(raw,dict) or not isinstance(raw.get('accounts'),list) or len(raw['accounts'])>128
                    or any(not isinstance(r,dict) for r in raw['accounts'])):
                raise ValueError('Invalid managed metadata')
            return {'default_account_id':raw.get('default_account_id'),
                    'accounts':[{'id':r.get('id')} for r in raw.get('accounts',[]) if isinstance(r,dict)]}
        if operation == 'list':
            raw = local.runner(['--app', 'codex', 'provider', 'list', '--json'])
            if (not isinstance(raw, dict) or not isinstance(raw.get('providers'), list) or len(raw['providers']) > 128
                    or any(not isinstance(p,dict) for p in raw['providers'])):
                raise ValueError('Invalid or oversized provider list')
            return {'currentProviderId': raw.get('currentProviderId'), 'providers': [
                {'id': p.get('id'), 'category': p.get('category')} for p in raw['providers'] if isinstance(p, dict)]}
        provider_id = arguments['id']
        if not isinstance(provider_id,str) or not provider_id or provider_id.startswith('-') or len(provider_id)>256:
            raise ValueError('Invalid provider ID')
        raw = local.runner(['--app', 'codex', 'provider', 'quota', provider_id, '--json'])
        if not isinstance(raw,dict) or raw.get('providerId') != provider_id or raw.get('app') != 'codex':
            raise ValueError('Quota provider identity mismatch')
        return {'id': provider_id, 'app': 'codex', 'quota': normalize_quota(raw, strict=True)}

    def _run(self, args, json_output=True):
        settings = self.data_dir / 'settings.json'
        configured = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
        executable = configured.get('executable') or os.environ.get('PAN_CC_SWITCH_CLI') or shutil.which('cc-switch')
        if not executable:
            raise CliError('cli_not_found', '未找到 GUI Fork 的 cc-switch 入口；请安装或在插件 settings.json 配置 executable')
        if getattr(self, '_verified_executable', None) != executable:
            try:
                version = self._call_cli(executable, ['--version'])
            except CliError as exc:
                if exc.code in {'cli_not_found', 'cli_unavailable'}:
                    raise
                raise CliError('cli_incompatible', '此入口不是兼容的 cc-switch GUI Fork；旧 CLI 已停用，请配置新入口') from exc
            if not isinstance(version, dict) or version.get('contract') != 'pan-cc-switch-gui/v1':
                raise CliError('cli_incompatible', '此入口不是兼容的 cc-switch GUI Fork；旧 CLI 已停用，请配置新入口')
            self._verified_executable = executable
        return self._call_cli(executable, args, json_output)

    def _call_cli(self, executable, args, json_output=True):
        remaining = getattr(self, '_deadline', time.monotonic() + 10) - time.monotonic()
        if remaining <= 0:
            raise ValueError('本次查询预算已用完；请刷新状态，勿自动重试切换')
        try:
            result = subprocess.run([executable, *args], capture_output=True, timeout=min(10, remaining),
                encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('CLI 超时，操作结果不确定；请重新读取状态，勿自动重试切换') from exc
        except OSError as exc:
            raise CliError('cli_unavailable', '无法启动已配置的 cc-switch CLI') from exc
        if result.returncode:
            # CLI errors can contain credentials; never relay raw stdout/stderr.
            raise CliError('cli_failed', f'cc-switch 执行失败（退出码 {result.returncode}）；请在本机检查 CLI')
        if not json_output:
            return None
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise CliError('cli_incompatible', 'CLI 未返回兼容的 JSON；请使用 cc-switch GUI Fork 的控制台入口') from exc

    def listing(self, app, panel=False):
        """Read the CLI list. Agent views hide every non-manually gated profile.

        Hidden rows are absent, never placeholders: their name, origin, notes,
        quota and category cannot leak through an agent-facing read, and a
        gated current selection is reported as `currentProviderId: null`. The
        Panel view keeps every row, the real current id, the gate and the
        settings revision.
        """
        raw = self.runner(['--app', app, 'provider', 'list', '--json'])
        if not isinstance(raw, dict) or not isinstance(raw.get('providers'), list):
            raise ValueError('不兼容的 provider list JSON')
        gates = self.settings.gates(app)
        cache_file = self.data_dir / f'quota-{app}.json'
        try:
            cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
        except (ValueError, OSError):
            cache = {}
        providers = []
        for row in raw['providers']:
            provider_id = str(row['id'])
            disabled = gates.get(provider_id, False)
            if disabled and not panel:
                continue
            origin = None
            try:
                url = urlsplit(row.get('apiUrl') or '')
                if url.scheme in {'http', 'https'} and url.hostname:
                    origin = f'{url.scheme}://{url.hostname}' + (f':{url.port}' if url.port else '')
            except ValueError:
                pass
            entry = {'id': provider_id, 'name': str(row.get('name') or row['id']),
                'origin': origin, 'isCurrent': row['id'] == raw.get('currentProviderId'),
                'notes': str(row.get('notes') or ''), 'category': str(row.get('category') or ''),
                'quota': cache.get(provider_id)}
            if panel:
                entry['nonManualDisabled'] = disabled
            providers.append(entry)
        accounts, account_error = [], None
        if app == 'codex':
            try:
                status = self.runner(['auth', 'status', '--json'])
                accounts = [{'id': str(a['id']), 'login': str(a.get('login') or a['id']),
                    'reauthRequired': bool(a.get('reauth_required', False)),
                    'isDefault': a['id'] == status.get('default_account_id')}
                    for a in status.get('accounts', [])]
            except ValueError as exc:
                account_error = str(exc)
        result = {'app': app, 'providers': providers, 'accounts': accounts,
            'currentProviderId': raw.get('currentProviderId'), 'accountError': account_error,
            'observedAt': int(time.time() * 1000)}
        if panel:
            state = self.settings.app(app)
            result['revision'] = state['revision']
            result['updatedAt'] = state['updatedAt']
        elif result['currentProviderId'] is not None and gates.get(str(result['currentProviderId']), False):
            # A manual Panel switch may select a gated profile. The agent view
            # must not confirm that id while its row is hidden, so the current
            # selection is reported as unknown; the Panel view keeps the real id.
            result['currentProviderId'] = None
        return result

    def invoke(self, operation, arguments):
        # Several CLI reads surround a write. Keep their combined deadline below
        # the host RPC deadline so the helper can report uncertainty explicitly.
        self._deadline = time.monotonic() + 24
        with self._lock():
            return self._invoke(operation, arguments)

    @contextmanager
    def _lock(self):
        # All helpers of this bridge (Panel and Session MCPs) share this fence.
        # The independently installed cc-switch is intentionally not modified.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / '.bridge.lock').open('a+b') as stream:
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            until = min(time.monotonic() + 5, self._deadline)
            while True:
                stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= until:
                        raise ValueError('其他 bridge 请求正在进行，请稍后刷新状态')
                    time.sleep(.05)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _invoke(self, operation, arguments):
        if operation == 'switch' and arguments.get('kind') == 'default_account':
            operation = 'default_account'
        app = arguments.get('app', 'codex')
        if app not in APPS:
            raise ValueError('不支持的应用')
        if operation == 'list':
            return self.listing(app)
        if operation == 'panel_list':
            return self.listing(app, panel=True)
        provider_id = arguments.get('id')
        if operation == 'quota' and provider_id is None:
            return self.listing(app)
        if not isinstance(provider_id, str) or not provider_id or provider_id.startswith('-') or len(provider_id) > 256:
            raise ValueError('无效的账号或配置 ID')
        state = self.listing(app, panel=True)
        if operation == 'default_account':
            if provider_id.isdecimal():
                raise ValueError('此 CLI 把纯数字解析为账号序号；不能按此 ID 安全切换')
            if app != 'codex' or provider_id not in {a['id'] for a in state['accounts']}:
                raise ValueError('ChatGPT 账号已不存在，请刷新列表')
            current = next((a['id'] for a in state['accounts'] if a['isDefault']), None)
            if current != arguments.get('expectedCurrentId'):
                raise ValueError('默认账号已变化，请刷新后再操作')
            if not any(a['id'] == provider_id and a['isDefault'] for a in state['accounts']):
                self.runner(['auth', 'default', provider_id], False)
                cache = self.data_dir / 'quota-codex.json'
                if cache.exists():
                    cache.unlink()
            updated = self.listing(app, panel=True)
            if not any(a['id'] == provider_id and a['isDefault'] for a in updated['accounts']):
                raise ValueError('CLI 返回后未确认默认账号；请刷新状态，勿自动重试')
            return updated
        if provider_id not in {p['id'] for p in state['providers']}:
            raise ValueError('配置已不存在，请刷新列表')
        disabled = self.settings.gated(app, provider_id)
        if operation == 'panel_config':
            if 'nonManualDisabled' not in arguments or 'expectedRevision' not in arguments:
                raise ValueError('缺少设置参数')
            if set(arguments) - {'app', 'id', 'nonManualDisabled', 'expectedRevision'}:
                raise ValueError('未知设置参数')
            if not isinstance(arguments['nonManualDisabled'], bool):
                raise ValueError('无效的开关取值')
            self.settings.update(app, provider_id, arguments['nonManualDisabled'],
                                 arguments['expectedRevision'])
            return self.listing(app, panel=True)
        if operation == 'panel_switch':
            # The Panel is the manual path: an explicit user choice may target a
            # gated profile; only the non-manual paths below are refused.
            return self._switch(app, provider_id, arguments, True)
        if operation == 'switch':
            self._require_ungated(disabled, '该配置已禁止非手动切换；请在 CC Switch 面板中手动启用或刷新列表')
            return self._switch(app, provider_id, arguments, False)
        if operation == 'auto_switch':
            self._require_ungated(disabled, '该配置已禁止非手动切换；自动切换不会改用其它配置')
            return self._switch(app, provider_id, arguments, False)
        if operation in {'quota', 'panel_quota'}:
            if operation == 'quota' and disabled:
                # Same refusal as a missing profile: a gated row must not be
                # confirmable by ID through an agent-facing read.
                raise ValueError('配置已不存在，请刷新列表')
            return self._quota(app, provider_id)
        raise ValueError('未知 bridge 操作')

    @staticmethod
    def _require_ungated(disabled, message):
        if disabled:
            raise BridgeError('non_manual_disabled', message)

    def _switch(self, app, provider_id, arguments, panel):
        state = self.listing(app, panel=True)
        if state['currentProviderId'] != arguments.get('expectedCurrentId'):
            raise ValueError('当前配置已变化，请刷新后再操作')
        if state['currentProviderId'] != provider_id:
            self.runner(['--app', app, 'provider', 'switch', provider_id], False)
        # Verification reads the full view: a Panel switch may legitimately
        # select a gated profile that the agent view hides.
        updated = self.listing(app, panel=True)
        if updated['currentProviderId'] != provider_id:
            raise ValueError('CLI 返回后未确认启用配置；请刷新状态，勿自动重试')
        return updated if panel else self.listing(app)

    def _quota(self, app, provider_id):
        raw = self.runner(['--app', app, 'provider', 'quota', provider_id, '--json'])
        quota = normalize_quota(raw)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / f'quota-{app}.json'
        try:
            cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        except (ValueError, OSError):
            cache = {}
        cache[provider_id] = quota
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
        return {'id': provider_id, 'app': app, 'quota': quota}


def normalize_quota(raw, strict=False):
    result = raw.get('result') or {}
    value = result.get('quota') or {}
    if strict:
        rows = value.get('tiers') if isinstance(value,dict) else None
        if (raw.get('status')!='ok' or raw.get('available') is not True or raw.get('error')
                or result.get('type')!='subscription' or value.get('success') is not True or value.get('error')
                or not isinstance(rows,list) or not 1<=len(rows)<=32
                or any(not isinstance(r,dict) or type(r.get('utilization')) not in (int,float)
                       or not math.isfinite(r['utilization']) or not 0<=r['utilization']<=100 for r in rows)):
            return {'status':'unknown','available':False,'queriedAt':raw.get('queriedAt'),
                    'tiers':[],'balances':[],'message':'Provider quota unavailable'}
    tiers = []
    if result.get('type') == 'subscription':
        for row in value.get('tiers', []):
            used = row.get('utilization')
            if isinstance(used, (float, int)) and not isinstance(used, bool) and math.isfinite(used):
                tiers.append({'name': str(row.get('name', '额度')), 'usedPercent': max(0, min(100, used)),
                    'resetsAt': row.get('resetsAt')})
    balances = []
    if result.get('type') == 'script':
        for row in value.get('data') or []:
            balances.append({k: row[k] for k in ('planName', 'remaining', 'used', 'total', 'unit') if k in row})
    return {'status': str(raw.get('status', 'unknown')), 'available': bool(raw.get('available')),
        'queriedAt': raw.get('queriedAt'), 'tiers': tiers, 'balances': balances,
        'message': '查询失败或凭据不可用' if raw.get('error') or value.get('error') else None}


def error_text(exc):
    """Return the Panel-visible error text, preserving a stable code."""
    if isinstance(exc, ValueError) and isinstance(getattr(exc, 'code', None), str):
        return json.dumps({'ok': False, 'error': {'code': exc.code, 'message': str(exc)}}, ensure_ascii=False)
    return str(exc) if isinstance(exc, ValueError) else 'Bridge 操作失败，请检查插件配置'


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    bridge = Bridge(os.environ['PAN_PLUGIN_DATA_DIR'])
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['method'] == 'provider.initialize':
                result = {'protocol_version': 'pan-capability/v1'}
            elif request['method'] == 'capability.invoke':
                params = request['params']
                value = bridge.invoke(params['operation'], params['arguments'])
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}],
                    'structuredContent': value, 'isError': False}
            else:
                raise ValueError('未知协议方法')
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as exc:
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': {'isError': True,
                'content': [{'type': 'text', 'text': error_text(exc)}]}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
