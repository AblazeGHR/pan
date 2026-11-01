# User-approved ordinal9 scope correction

Decision: `job-unknown-api-reject-approved-20261031`.

Authority: `D:/project/Pan/.workflow/job-unification-review-20261028/UNKNOWN_API_POLICY_AMENDMENT_20261031.md`.

Only the latter half of original `test_action_template_unknown_falls_back_to_assign`, which force-writes unknown legacy `action.api` and expects implicit assign, is explicitly cancelled. The old test and raw red remain unchanged. This is no longer a pending decision or a missing fallback to implement. Known assign/send/QQ/shell and arbitrary command capabilities are unaffected.

Source inspection at the fixed r26 validation SHA identifies existing rejection seams: `packages/jobs/api.py::_normalize_scheduled_action` explicitly rejects unknown APIs; canonical Action references use existing `FinalService._references` validation. No dispatcher or new execution mechanism is needed. Existing migration inventory preserves unsupported action records rather than executing them; inventory classification alone does not prove source-byte preservation or runtime zero effects.

After the immutable QQ gates terminated (8 standalone and 31 same-process nodes passed), exact owned successors were added for create/update rejection before operation/definition writes, unknown reference rejection, preserved synthetic legacy source bytes through cold startup, and zero queue/Run/Attempt/effect/implicit assign. Mapper implementation/tests remain read-only. The active QQ validation HEAD was not moved until its terminal notice. The new versioned matrix preserves every original assertion and the historical rev25 matrix.

Current status: five policy nodes passed at `38b60e270c060a086705cbd5eb0b149f3d75441f`, exit0, with exact commands/raw hashes in `unknown-policy.command.json`. There is no product change. The preceding QQ source `9ccf4f2184851a7a67f60823b0c733d47d1386e6` is unchanged except for the added policy-only test file. This proves bounded reject/preserve/no-effect behavior; no real legacy data was read or migrated.
