# CP-X2 CI40750300694 Windows launcher-shape repair

Frozen combination baseline: `fc5604ab8fc471f04fe91a868ca6938a11e48123`.
This original TA worktree normally fast-forwarded from clean `2817e48a`; the MA
combination worktree was read-only and was not edited. Final source:
`dd3ea8d8f9f9daa9d460a9bb42e63e800324c34b`.

## Diagnosis

Both remote Windows owned-combination jobs had 21 nodes: 20 passed, one failed.
The sole node in Python 3.12 and 3.14 was
`tests/test_platform_process_windows_hidden.py::test_hidden_tree_identity_stdio_and_desktop_cleanup[named-venv]`.
The raw 3.12 trace identifies business PID **5520** and owned root **5520**, whose
parent is 788. The 3.14 trace identifies business PID **2124** and owned root
**2124**, whose parent is 5284. The failed assertion expected an extra parent
layer solely because the parameter was named `named-venv`. The selected
`sys.executable` can be a directly installed CI interpreter. Interpreter location
is not a process ownership proof and does not require a redirector.

This is a test assumption defect, with no demonstrated platform product defect.
A local exact-fc5604ab test replay with `sys.executable=sys._base_executable`
reproduces line 88 (exit 1); the original failed test source is compiled unchanged
from Git in the isolated child. Original remote XML and raw failed log ZIP are
preserved under original-remote/ with source hashes in remote-manifest.json.
The MA tree/source/CI were not changed or rerun.

## Minimal correction and retained safety

Only two owned files change:

- tests/test_platform_process_windows_hidden.py: remove the branch on interpreter
  label and prove the actual relationship using the existing read-only
  `packages.jobs.window_guard.owned_descendant_handle`. Validate the retained
  root handle against its original FILETIME first. This existing primitive uses
  a real ancestor snapshot, retains native handles, checks monotonic birth times
  and compares the re-opened root birth against the retained root birth. For
  same PID, exact business FILETIME must equal the original root FILETIME; for
  a descendant, an actual ancestor path back to that exact lifetime must exist.
  The returned business handle must match the fixture's FILETIME and belong to
  the exact owned Job. Retain root and business handles through death proof.
- tests/helpers/process_owner_hidden_probe.py: publish the complete identity JSON
  via write-close/os.replace. A first combined run exposed the existing
  exists-before-write-completes race as JSONDecodeError. This red is retained in
  source-28764e26/combination.*; no sleep, JSON-error suppression, timeout increase
  or assertion weakening was used.

Original real desktop placement, active input desktop, exact FILETIME, Job root
membership, all business member PIDs in the Job, kill_tree.confirmed, all retained
process lifetimes dead, desktop_gone and final cleanup assertions remain.
Negative tests reject business birth mismatch, original root birth mismatch, and
an unrelated owned root. A separate direct-business-root successor runs the
same original tree/desktop/cleanup test via the directly installed interpreter;
the specified Pan-main venv also exercises a real redirector ancestor path.
The original parametrized node names and skip remain unchanged. Four additional
nodes make the combined suite 25 nodes, with no new skip or xfail.

No product files, public schema, shared execution_host, CP-X1 source/tests,
controller claim order, epoch/generation/lease/unknown boundary, POSIX/Darwin,
server/worker/ledger or workflow/CI file was edited.

## Final source-bound validation

Interpreter: `D:/project/Pan-main/.venv/Scripts/python.exe`, Python 3.14.5,
MSC v.1944 64-bit. All calls use `scripts/run_tests_isolated.py`; exact commands,
start source SHA, exit and duration are recorded by run_validation.py in JSON.

```
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-ci40750300694/run_validation.py combination
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-ci40750300694/run_validation.py safety
```

- combination: all three requested files, plus the strict original desktop option
  `-p tests.support.lifecycle_strict_options --cpx1-require-hidden-desktop`:
  **25 passed, 0 skipped, exit 0** (2 existing dependency deprecation warnings).
- safety: original ProcessOwner shared contract, POSIX unit negatives, hidden
  token, cleanup failures and retained safety protocol:
  **139 passed, 19 original platform skips, exit 0**.
- Final-source desktop proof files distinguish `rootIsBusiness=true` from the
  actual venv ancestor case, with original FILETIMEs and both allDead and
  desktopGone true. Controller/followthrough original public Attempt/effect/tree
  and desktop assertions pass without editing their tests.

An earlier additional P23 owned/canonical/public/final-executor group started on
28764e26 before the atomic publication-only fixture correction. It is retained
separately and is not represented as a final dd3ea8d8 run. These four P23 files
and their product dependencies did not change in the fixture-only correction.
No production test assertion was weakened to obtain the final combination pass.

## Remote reproduction / remaining gate

MA should run the existing Windows owned-combination job on Python **3.12 and
3.14** at the candidate combination SHA, using its current command:

```
python scripts/run_tests_isolated.py -- python -m pytest tests/test_lifecycle_followthrough.py tests/test_lifecycle_controller_publication.py tests/test_platform_process_windows_hidden.py -q -ra --junitxml=owned-combination.xml
```

A stronger equivalent retains
`-p tests.support.lifecycle_strict_options --cpx1-require-hidden-desktop`.
Expected: **25 passed, zero failed/skipped** (previous 21 plus four negative/direct
successor nodes). Local Python 3.14.5 cannot establish remote 3.12/3.14 green;
remote rerun remains MA-owned. No push, main/practical, deployment, protected
service, real data/profile or 8767/8768 operations occurred. No new TA was made.

## Archived cleanup and supplementary regression

The supplementary P23 owned/canonical/public/final-executor group passed
**46 nodes, zero failures/skips, exit 0**, starting at 28764e26; the start SHA is
retained in p23.json and is explicitly not final-source evidence.

Final runtime proof archive includes 29 JSON files (13 P23, 7 original
controller/followthrough, and 9 hidden-process proofs including unchanged
historical proofs). Each retains its own source metadata. Only the final-source
files are used for dd3ea8d8 direct/ancestor and cleanup claims. After byte-identical
copy checks, 23 tracked generated files were restored to dd3ea8d8 and three exact
archived untracked proof files were removed after absolute path containment
verification. No CP-X1 source or historical evidence content was overwritten in
the candidate. See runtime-archive.json for the exact paths and hashes.

Final cleanup confirms 13 final-source proof-member lifetimes are dead/absent
(or the current PID has a different FILETIME), all recorded actual private
desktops are gone, and no owned fixture helper process remains. See cleanup.json.
No bare-PID kill or cleanup of unrelated processes was performed.

The full candidate changes only the two owned test/fixture files plus this
self-contained evidence directory. No target-platform results from previous
CI40711485368 are substituted for this Windows gate. Evidence-only commit after
dd3ea8d8 adds no product/test change. Worktree is delivered clean.
