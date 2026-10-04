# Agent Job directory convenience

User authorized arbitrary working directories for Jobs on 2026-10-04. The small
change removes only the project-containment check: an existing directory is
still required and resolved paths are persisted. Relative paths retain their
service-cwd interpretation; agents should use absolute paths. Both process and
scheduled-shell validation use the same check. Session access and notification
routing are unchanged. Remote security is separate, not certified here.

Focused regression: 60 passed (`job-external-cwd-regression-uv.xml`). One added
test uses the production Runner entrypoint and a real Python subprocess in an
external, non-ASCII directory, checking its actual cwd, log, exit code and a
single terminal-notification projection into an isolated Session adapter. This
does not claim the isolated adapter is the deployed notification transport.

Actual deployed probe: `job_1d0e55dccc2cce3f632ad224`, creator and target both
`ses_be105a81379e8cb1`, cwd `D:/project/Pan`. Registry reports completed, exit 0,
notificationState delivered, terminal event
`job_1d0e55dccc2cce3f632ad224:terminal`. The user confirmed delivery and clarified
that this Agent must be idle to receive it. The probe logs only a static marker
and cwd; it does not modify project files or involve a TA.

The deployed probe used the old allowed cwd. This candidate's external-directory
change is not yet deployed; no canonical branch advance or service restart was
performed. Until deployment, a Job can launch an explicitly authorized command
wrapper which runs its test subprocess in the candidate worktree. This is not
evidence that the old service's direct cwd validation has changed.

Operational rule: launch long work, record Job ID and expected result path, then
end the Agent turn when there is no independent work. On the terminal notice,
inspect exit code and machine-readable results before accepting the work.
