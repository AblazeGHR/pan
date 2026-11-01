# CI 408431 console output repair

The exact CI freeze `a95d7313514e411d06b71fe23bb529d4045633f8` was merged into
this branch. CI 40843148469 Windows 3.12 and 3.14 focused XML respectively
have SHA-256 `582e52ac245b517a6f8a30ae1f746443802fb1862d6ad8274d0f3b8b9c703c68`
and `ab466984ea74feb573e863850b238e1f78a7e974a1b13ea44a9eba5e1a8e49db`.
Both preserve the original main startup `UnicodeEncodeError` at the console
prompt, before listener readiness, and are unchanged in the MA evidence tree.

The focused negative control on the unmodified freeze used real strict cp1252,
ASCII, and UTF-8 streams. Six cases yielded four failures: cp1252 and ASCII
raised UnicodeEncodeError at the original prompt; missing stdout lost the prompt;
both missing streams silently lost it. The red XML is
`evidence/cp-x1-ci408431-negative.xml`.

The output-only change writes through the existing stdout, or stderr when
stdout is absent. It escapes only glyphs unsupported by the stream's declared
encoding, leaves supported Unicode intact, and raises for absent/broken streams
instead of hiding an I/O error. It does not change process-wide encodings,
console lock/reader, q timing, payloads or lifecycle behavior. Injected output
callbacks remain supported. A successful API acceptance remains latched if
subsequent display fails, while that display failure propagates.

Windows local source `360322d3351f244701f1d38459cbd245ffe2c0d5`, via
`scripts/run_tests_isolated.py`, produced 53 passed for the dedicated strict
encoding tests and existing console/HTTP/CLI tests, and 43 passed for genuine
main restart, canonical tree, preparation, strict lifecycle and plugin lifecycle.
After adding the prompt observation assertion, source
`78a337b6855be4ab60f8b9039a26ea3e1e9d6ac1` passed the genuine restart node.
That real owned child explicitly used cp1252:strict and the test observed the
escaped original Chinese prompt before verifying successful original-effect
restart, identity, readiness and owned port release. JUnit and structured
proof are in `evidence/cp-x1-ci408431-*`.

The 408431 three-platform gates and focused outcomes are preserved as prior CI
evidence. The new source has not run remotely. MA owns exact combined SHA
target validation; the historic full-suite red cohort is still open.
