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


class Bridge:
    def __init__(self, data_dir, runner=None):
        self.data_dir = Path(data_dir)
        self.runner = runner or self._run

    def _run(self, args, json_output=True):
        settings = self.data_dir / 'settings.json'
        configured = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
        executable = configured.get('executable') or os.environ.get('PAN_CC_SWITCH_CLI') or shutil.which('cc-switch')
        if not executable:
            raise ValueError('未找到 GUI Fork 的 cc-switch 入口；请安装或在插件 settings.json 配置 executable')
        if getattr(self, '_verified_executable', None) != executable:
            version = self._call_cli(executable, ['--version'])
            if not isinstance(version, dict) or version.get('contract') != 'pan-cc-switch-gui/v1':
                raise ValueError('此入口不是兼容的 cc-switch GUI Fork；旧 CLI 已停用，请配置新入口')
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
            raise ValueError('无法启动已配置的 cc-switch CLI') from exc
        if result.returncode:
            # CLI errors can contain credentials; never relay raw stdout/stderr.
            raise ValueError(f'cc-switch 执行失败（退出码 {result.returncode}）；请在本机检查 CLI')
        if not json_output:
            return None
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError('CLI 未返回兼容的 JSON；请使用 cc-switch GUI Fork 的控制台入口') from exc

    def listing(self, app):
        raw = self.runner(['--app', app, 'provider', 'list', '--json'])
        if not isinstance(raw, dict) or not isinstance(raw.get('providers'), list):
            raise ValueError('不兼容的 provider list JSON')
        cache_file = self.data_dir / f'quota-{app}.json'
        try:
            cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
        except (ValueError, OSError):
            cache = {}
        providers = []
        for row in raw['providers']:
            origin = None
            try:
                url = urlsplit(row.get('apiUrl') or '')
                if url.scheme in {'http', 'https'} and url.hostname:
                    origin = f'{url.scheme}://{url.hostname}' + (f':{url.port}' if url.port else '')
            except ValueError:
                pass
            providers.append({'id': str(row['id']), 'name': str(row.get('name') or row['id']),
                'origin': origin, 'isCurrent': row['id'] == raw.get('currentProviderId'),
                'notes': str(row.get('notes') or ''), 'category': str(row.get('category') or ''),
                'quota': cache.get(str(row['id']))})
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
        return {'app': app, 'providers': providers, 'accounts': accounts,
            'currentProviderId': raw.get('currentProviderId'), 'accountError': account_error,
            'observedAt': int(time.time() * 1000)}

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
        provider_id = arguments.get('id')
        if operation == 'quota' and provider_id is None:
            return self.listing(app)
        if not isinstance(provider_id, str) or not provider_id or provider_id.startswith('-') or len(provider_id) > 256:
            raise ValueError('无效的账号或配置 ID')
        state = self.listing(app)
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
            updated = self.listing(app)
            if not any(a['id'] == provider_id and a['isDefault'] for a in updated['accounts']):
                raise ValueError('CLI 返回后未确认默认账号；请刷新状态，勿自动重试')
            return updated
        if provider_id not in {p['id'] for p in state['providers']}:
            raise ValueError('配置已不存在，请刷新列表')
        if operation == 'switch':
            if state['currentProviderId'] != arguments.get('expectedCurrentId'):
                raise ValueError('当前配置已变化，请刷新后再操作')
            if state['currentProviderId'] != provider_id:
                self.runner(['--app', app, 'provider', 'switch', provider_id], False)
            updated = self.listing(app)
            if updated['currentProviderId'] != provider_id:
                raise ValueError('CLI 返回后未确认启用配置；请刷新状态，勿自动重试')
            return updated
        if operation == 'quota':
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
        raise ValueError('未知 bridge 操作')


def normalize_quota(raw):
    result = raw.get('result') or {}
    value = result.get('quota') or {}
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
                'content': [{'type': 'text', 'text': str(exc) if isinstance(exc, ValueError)
                    else 'Bridge 操作失败，请检查插件配置'}]}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
