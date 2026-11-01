# Original CI failure roots: history completeness and POSIX original identity

Old exact1cec CI41356342496 and all failure JUnits are preserved in ../ci-41356342496. This followthrough changes only dependency checkout/test wiring, never canonical CAS/identity/owner/recovery policy or platform skips.

1. actions/checkout fetch-depth0 retains the actual historical commits required by original unchanged baseline tests (9b4506c9 original uncooperative legacy writer,4b577633 old stable-reset characterization). No skip or replacement fixture for gitshow; both tests run locally and pass.4b577633 verified an ancestor of candidateHEAD, so full ancestry fetch supplies that evidence. Applied to test.yml checkouts for consistent job provenance.
2. POSIX product-default detached executor test now distinguishes an original running Attempt/effectAdmission (terminal effects must still be empty) from already completed effect. Both bind the exact original Attempt/effectKey, then still await actual native succeeded receipt, cleanup confirmation, bootstrap/supervisor exit, unchanged single Attempt/effect and exactly one execution. No success inferred from pending/unknown and no provider adapter replaced.
3. POSIX lost-leader log test now calls existing recover_external_receipts for the already observed original native unknown receipt before taking expectedStateRevision. It asserts uncertain and original native Attempt unchanged before explicit cancellation; original no-replay, subtree-live unknown, log bytes, receipt and operation identity assertions all remain. Production CAS is untouched; this removes a deliberately stale revision race in fixture observation order.

Local Windows verification2passed2originalplatformskip. These two POSIX nodes must actually run and pass in new Ubuntu/macOS CI; Windows skip is not remote acceptance.

Previousfa44 frozen local combination formally239pass(contract/crash),79pass2existing skip(terminal),118pass(real notificationHTTP/selected worker producers/root), sourceDrift[]. No stale Worker/retry tasks observed in its per-node after-teardown journal. Original full1cec cross-loop residual is still unlocalized; a new full run retains the journal to identify any producer, not clear state or suppress shutdown failure.3self-generated notification outputs privately hash-backed before restoring only those tracked outputs. Full evidence in ../cohortfa44-terminal.

No main/practical/source authority or service effects. New exactSHAfullCI and localfull remain required; earlier262original successor observations do not certify full composition green.
