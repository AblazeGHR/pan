# Revision14 follow-up — Windows quoted shell diagnostic

The retained quoted-shell failure is a real loss relative to the old Windows shell path. It belongs to the command-line serialization edge between explicit cmd argv and native process creation. No product file was changed in this follow-up. The original positive assertion remains unchanged and fails; all previous r14 implementation and evidence remain intact.

Starting clean HEAD: `d350efaf87e6ae60a5a60f037a4d736b52a2243b`. Tested source HEAD: `4c527d3cddc64e8d3428997d05d2b972da74d446` (one new owned diagnostic test only). Same worktree and branch `r6/retirement-parity-acceptance-20261029-a`. Final evidence commit changes only this report and versioned artifacts. No other branch input, remote CI, service or real data was used.

## Original and current call chains

Old scheduled shell validates/stores command/cwd in `scheduler.store.create_task` and `background_jobs.validate_shell_action`. The actual hidden execution edge in `core.background_runner._run_legacy::spawn` passes the raw `shellCommand` into retained `DesktopCommand(..., shell=True)`. Its Windows implementation `core/platform/desktop_win.py::DesktopCommand.__init__` line 81 constructs `list2cmdline([COMSPEC]) + ' /c "' + raw_command + '"'`. Only the executable prefix is CRT-serialized. The command-language tail is preserved inside an extra quote pair. It invokes the same retained `process_creation_win.create_owned_process` with a private desktop and creation-time Job membership. Visible legacy execution uses `subprocess.Popen(raw_command, shell=True)`; this diagnostic did not exercise that visible branch or reopen the fenced old writer/dispatcher.

Current production chain is React simple shell form → `FinalService._normalize_template_form` → `normalize_legacy_form_fields` → platform `shell_command_argv` → immutable process Action/frozen plan → existing process provider/independent host → `WindowsProcessOwner.spawn` → `OwnedPopen._execute_child` → `list2cmdline(values['args'])` → `create_owned_process`. `WindowsProcessOwner.spawn` normalizes argv to ordinary strings; no raw-shell metadata/string API reaches `_execute_child`. The r14 command and raw bytes remain intact through the form/helper; serialization then inserts literal backslashes before embedded quote characters.

## Reproduction and observed bytes

Use the designated interpreter and isolation wrapper:

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-rev14-shell-diagnostic-20261029/run.py
```

The new test `tests/test_retirement_rev14_shell_diagnostic.py::test_same_raw_command_legacy_shell_vs_current_crt_serialization` compares actual retained native processes, not simulated command results. Its recording wrapper forwards every native-create argument unchanged. The exact same original command is used in the shell modes, in separate temporary working directories:

```text
"D:\project\Pan-main\.venv\Scripts\python.exe" -c "from pathlib import Path; Path('shell-result.txt').write_text('owned')"
```

| Real owned path | Exit | Marker | Native handles closed |
|---|---:|---|---|
| Old `DesktopCommand(raw, shell=True)` | 0 | `owned` | yes |
| Current helper argv + `WindowsProcessOwner` | 1 | absent | yes |
| Same current argv with only `/s` removed | 1 | absent | yes |
| Ordinary direct Python argv for the same child program | 0 | `owned` | yes |

Actual native-create strings below are literal strings, not JSON-escaped representations:

```text
old:     C:\Windows\system32\cmd.exe /c ""D:\project\Pan-main\.venv\Scripts\python.exe" -c "from pathlib import Path; Path('shell-result.txt').write_text('owned')""
current: C:\Windows\system32\cmd.exe /d /s /c "\"D:\project\Pan-main\.venv\Scripts\python.exe\" -c \"from pathlib import Path; Path('shell-result.txt').write_text('owned')\""
```

`native-lines.json` and the `SHELL_DIAGNOSTIC=` line in `focused.log` record the exact raw command, each full native command line, UTF-16LE hex and SHA256, plus process log bytes. Windows CreateProcess consumes a Unicode command line, so UTF-16LE code units are the byte comparison; no console encoding interpretation is needed. The current stderr identifies the backslash-quoted executable as unrecognized. Raw error bytes are retained, not mapped into a success or altered command.

Microsoft documents that `/s` with `/c` removes the outer quote pair while leaving the inner command unchanged; `/c` consumes a command string. Python documents list-to-string conversion using MS C runtime rules, which escape quote characters with backslashes. Those are different grammars. Removing `/s` does not remove the inserted backslashes, and this is confirmed by the second real failure. Sources: [Microsoft cmd specification](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd), [Python subprocess Windows sequence conversion](https://docs.python.org/3/library/subprocess.html#converting-an-argument-sequence-to-a-string-on-windows). The designated Python 3.14.5 runtime's actual `list2cmdline` output is recorded independently of the documentation version.

## Why a helper-only repair stops

Every embedded quote in the unchanged final command argv element is escaped by the downstream CRT serializer. Adding an outer quote pair in the helper adds more escaped quotes; removing shell flags leaves the wrong quoting grammar. The process schema accepts argv, the normalizer enforces a list of ordinary strings, and ProcessOwner constructs another ordinary string list. A raw command-line marker/subclass cannot survive that contract. Encoding the command, putting it in a temporary script/environment variable, switching shells or adding a wrapper process would change the agreed execution/identity surface and is not a minimal argv correction. No such workaround is implemented.

The narrow next method scope is `packages/core/platform/process_creation_win.py::OwnedPopen._execute_child`, specifically its one serialization expression at line 137 and an optional module-private pure `_owned_command_line` helper. The proposed diff is retained in `evidence/job-retirement-rev14-shell-diagnostic-20261029/proposed-native-serializer.diff`; it is **not applied or acceptance-tested**. It recognizes only the exact existing five-element host-cmd `/d /s /c` envelope and builds `CRT(prefix) + ' "' + raw_command + '"'`; every other argv follows the original serializer. Exact interpreter matching against COMSPEC/custom executable and inherited child environment is an explicit review point. Do not broaden to any executable merely named cmd.exe, arbitrary `/c` forms or all argv values.

A reviewable serializer fix must retain the original r14 positive test unchanged and prove it green. Affected regression scope: ordinary argv including spaces, literal quotes/backslashes/Unicode/empty arguments; nonmatching executable/flag forms retain byte-for-byte CRT output; original raw command with quoted executable, cwd and shell metacharacters; hideWindow true/false; real creation-time Job membership, restricted-token/private-desktop behavior, failure before resume, partial cleanup and inherited stdio. Suitable existing regression leads are `test_platform_process_windows_hidden.py`, `test_cpx1_creation_edges.py`, `test_hidden_job_token.py`, `test_job_hidden_production.py` and process-log/canonical process tests. No such broad matrix is rerun before the product scope is authorized. Public ProcessOwner API, token/native flags/handles, cleanup and canonical retry/ledger methods need no proposed edits.

## Actual verification and delivery boundary

At immutable tested SHA above, the two-node run finished in **2.124 seconds**, Python **3.14.5**: **1 diagnostic passed / 1 original positive failed / 0 skipped**, actual **exit 1**. `command.json` retains exact argv/source/counts and `focused.xml`/`focused.log` retain raw results. The diagnostic pass proves the observed difference; it is not a strict successor closing quoted shell capability. No assertion in `test_retirement_rev14_templates.py` was edited. No original r14 evidence was overwritten and no repeated large matrix/remote CI was run.

All four comparison children close via the original owned handle/desktop/Job cleanup paths; the retained canonical red also closes its original owned host. No ports, browser, real profiles, real data, accounts or existing services were used. The pre-existing ignored dependency junction from r14 remains untouched; its earlier blocked cleanup is not retried. M07 quoted shell remains an explicit product gap awaiting MA scope authorization. The rest of r14 stays at its prior bounded evidence status; clean Git is not full migration or shell acceptance.
