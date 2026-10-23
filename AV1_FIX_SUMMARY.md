# AV-1 Fix Summary

## Issue
Deterministic permanent validation failures (ValueError from boundary checks) were incorrectly marked as `interrupted` (recoverable) instead of `failed` (terminal), causing:
1. Failed operations to retry indefinitely
2. No permanent failure evidence
3. Unclean separation from transient interruptions

## Solution
Modified `_resume_unlocked()` in `src/workflows/memory_store.py`:

### Key Changes
1. **Skip failed steps** (line 143): Added `"failed"` to the skip condition so failed steps are never re-executed
2. **Separate ValueError handling** (lines 150-155): Catch ValueError specifically and mark step+record as terminal `failed`
3. **Keep transient handling** (lines 156-160): Other BaseException (KeyboardInterrupt, OSError) remain `interrupted` and recoverable

### Permanent Validation Failures Covered
- Target/source symlink checks
- Non-object JSON source validation
- Unsupported operation action
- Store path escape validation (caught at build time)

## Tests Added
1. **test_deterministic_validation_failure_marks_record_failed_and_blocks_retry**: Verifies symlink validation fails permanently and blocks retries
2. **test_unrelated_operation_completes_after_permanent_failure**: Ensures failed records don't poison unrelated operations

## Verification
- All 12 tests in test_memory_operations.py pass
- Manual test driver confirms permanent failures mark as `failed` and transient failures remain `interrupted`
- Boundary condition tests pass for all permanent validation failures
- Code compiles without errors

## Behavior Preservation
✓ Crash/interruption handling: Unchanged
✓ Repeated recovery: Works correctly
✓ Multi-step operations: Unaffected
✓ Lock semantics: Unchanged
