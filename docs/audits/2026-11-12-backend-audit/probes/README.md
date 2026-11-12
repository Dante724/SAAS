# Audit reproduction probes

These are the scratchpad tests the 12 Nov 2026 audit used to reproduce its findings. They live here as evidence. They sit outside `backend/tests`, so CI never collects them.

## Running them

From the repo root:

```bash
ENVIRONMENT=test venv/bin/python -m pytest -p backend.tests.conftest -p no:cacheprovider -c pytest.ini --rootdir=. docs/audits/2026-11-12-backend-audit/probes/<file>.py
```

## What a pass or fail means

The two kinds of probe read in opposite directions:
- `test_probe_inv_lab.py` asserts the **correct** behaviour. It failed on the audited snapshot.
- The other probes assert the **buggy** behaviour. They passed on the audited snapshot.

## Once a bug is fixed

When a finding is fixed, its proper regression test lives in `backend/tests/`, and the matching probe here is expected to flip. P0 fix PRs:

| PR | Findings fixed |
|---|---|
| #207 | INV-01 |
| #208 | WO-01, NTF-01, CLI-01 |
| #209 | LAB-01–04, RPT-01, WO-02 |
| refunds/cash legs | FIN-01, FIN-02 |
