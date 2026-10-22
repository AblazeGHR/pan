# Installed Codex native config fixture

`real_codex_0163_provider_config.json` is a lossless selection of the
`model_provider` and `model_providers` fields from the actual installed
`codex-cli 0.163.0` config/read result, captured on 2026-10-22 in
`artifacts/provider-launch-real/native-probe.json`. It is not output from
`fixed_codex_server.py`. No real profile/auth was read: that diagnostic used a
new empty temporary CODEX_HOME, file-only credential storage and explicit
`-c` provider overrides pointing at loopback port 1; it made no model turn.
Only unrelated config fields were omitted from this fixture. Null/default field
shapes are unchanged. The provider URL is the exact owned diagnostic value.
The full real start/resume/materialized-branch proof is the opt-in integration
test and its signed handshake/loopback HTTP evidence, not this JSON fixture.
