# ADR stub: COT vintage provenance

> **This decision is recorded as crucible-stack ADR-0008**, beside ADR-0007, because
> its scope half — *is vintage provenance inside narrowed cotdata's boundary?* — is an
> interpretation of ADR-0007 and must be discoverable from ADR-0007's own directory.
> This file is a local pointer so the cotdata worktree still surfaces the decision.

**Decision, in one line:** vintage provenance IS in scope for the narrowed `cotdata`
(it is CFTC-positioning provenance), and it persists in the existing Parquet + manifest
contract — no database.

- Full ADR: `crucible-stack/docs/adr/ADR-0008-cot-vintage-provenance-in-parquet.md`
- Design/working notes: [../design/cot_vintage.md](../design/cot_vintage.md)
