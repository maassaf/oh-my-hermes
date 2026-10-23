# Optional domain science toolkits

Describe the result to Hermes; no domain CLI belongs in the normal OMH setup
flow. These installed skills include runnable, optional companion scripts, not
an OMH execution service. Loading, routing, setup, update and doctor only read
reviewed UTF-8 resources. They never install science packages or run a helper.

| Ask Hermes | Installed skill | Actual backend and result |
| --- | --- | --- |
| Intersect supplied polygon layers in an explicit metric CRS and measure overlap. | `omh-geospatial-analysis` | Direct Shapely/PyProj; result GeoJSON, metric summary and replay manifest. No GIS-MCP, map or raster claim. |
| Cross-check public variant assertions against ClinVar and trial registry records while retaining conflicts and missing dates. | `omh-biomedical-evidence` | Explicit bounded host-terminal BioMCP capture followed by offline stdlib reconciliation. No patient data, eligibility or treatment decisions. |
| Run a bounded rigid-receptor search for prepared PDBQT inputs and an explicit binding box. | `omh-molecular-docking` | Standalone AutoDock Vina Python; actual poses, finite scores and replay manifest. No ToolUniverse execution or efficacy/safety inference. |

Each compact skill points to `references/execution-and-qa.md` and
`toolkit/README.md`. The full procedure is on demand. Optional dependencies
belong in an explicitly selected isolated environment, not OMH core. Missing
packages, invalid inputs or timeouts are failures, never guessed findings.
Outputs must use new directories and be read back with hashes before success.

## Observed scope and limitations

The producer observations on October 22, 2026 are different evidence classes:

- [Geospatial analysis](geospatial-analysis.md): actual Shapely/PyProj execution
  on declared synthetic rectangles, with independent analytical area checks.
  The fixture is not a public-world location or risk finding.
- [Biomedical evidence](biomedical-evidence.md): actual bounded BioMCP retrieval
  from both requested public sources. Both were available, but reconciliation
  remained **partial**, exit 3, because source dates and literal disease-label
  equivalence were unresolved. Retrieval success is not complete reconciliation.
- [Molecular docking](molecular-docking.md): actual isolated Vina 1.2.7 native
  search and seeded replay of rights-audited external 1IEP inputs. Source
  inspection of ToolUniverse is not backend execution. Reproducibility is
  limited to the recorded version/platform/settings.

None of these observations proves general scientific validity, clinical
applicability or availability on another machine. Source responses and docking
inputs/results are not bundled. Code licenses do not grant provider-data rights.
The toolkits carry their own exact resource/provenance inventories; no copied
upstream skill pack or native binary is installed.

## Distribution and maintenance boundary

Canonical definitions and closed support factories drive native setup, explicit
local-source import, tap output and the single portable inventory. Six clone
adapters consume that inventory without host-specific copied resource maps.
The wheel and source distribution include exact optional companion data; OMH
runtime dependencies remain empty. Windows line endings are explicitly LF for
canonical and projected companion bytes.

Native updates preserve edited owned companions without explicit force, refuse
foreign collisions even with force, and remove only unchanged owned retired
files for the selected owner. Python portable installation also preserves
edited retired files. Clone adapters preserve retired files conservatively,
including unchanged ones; automatic retired-file cleanup is not claimed for
those adapters. POSIX adapter execution is tested; PowerShell execution on
Windows requires its own host evidence.

English deterministic routing and explicit canonical/display-name invocation
are local checks, not live Hermes semantic nomination. No Korean trigger table
was expanded, and natural Korean semantic selection remains untested. Existing
research, paper explanation, general tabular analysis and host MCP setup retain
their own scope. An installed skill or prepared wrapper card is not execution.

## Agent and maintainer references

The domain pages contain explicit optional setup and helper invocation recipes
for an accepted task. The [implementation contract](implementation-contract.md)
records the design and acceptance boundary, not an execution receipt. Follow
the maintainer checklist in `docs/ADDING-A-SKILL.md` for catalog, routing and distribution
changes. The parent task's independent verifier owns final acceptance; local
checks are not review, CI, publication or merge evidence.
