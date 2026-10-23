# Show-reel: optional local motion/audio toolkit

Ask Hermes to make an audience-specific product showcase from approved sources.
`omh-show-reel` provides the original production method, evidence/rights gates,
three-concept comparison, per-segment direction and weakest-scene revision.
The same installed skill also carries **optional executable companion data**:
upstream's time-pure engine (`REEL` scenes, cues, beats, kinetic type helpers,
`FX` camera/impact/wipe/floor/dust/rays/flash/letterbox/vignette/grain/HUD/subtitle
layers and an API example), the upstream project layout, local score/SFX,
music analysis, cue-aligned mix and stems, exact-frame local capture with
supersampling and sub-frame motion blur, contact sheets, image prep, finishing
and measured technical/motion diagnostics. The `references/craft-*.md` pages are
complete English translations of upstream's craft references (workflow, brief,
story, audio, images, design, engine, techniques, QA). An example scene does not
complete a commissioned film.

## Where the real tools are

Use a file reader relative to the loaded skill's base directory to read
`toolkit/README.md`, then only the scripts/examples selected for the production.
Native Hermes exposes `skill_dir`; use `read_file` on files beneath it if its
linked-files index does not enumerate the toolkit. Portable hosts use ordinary
file tools and their loaded base directory, not Hermes-only commands.
The README contains the complete operator recipes, API, bounds and failure modes.

All five paths carry identical reviewed bytes: repository tap, managed native
install/update, explicit local-source pack conversion, portable Agent Skills
(and the six copy-only host adapters), and built wheel package resources. OMH
uses one portable inventory, `agent-skills/.omh-agent-skills-manifest.json`,
not copied per-host digest tables. Native installs track companion hashes in a
separate backwards-compatible `support_files` ownership field; legacy missing
support is stale/missing, not falsely fresh. Edits/unowned collisions are
preserved/refused before writes, curator-kept skills carry their support ownership
across generations, and only unchanged previously owned retired support is pruned.
An explicit portable reinstall retains its documented replacement behavior for
currently managed files; edited retired support is left as foreign data.

## Production authority and prerequisites

OMH core remains stdlib-only, with no renderer dependency, import of companion
scripts, new render command, network call or hidden execution. The kit's opt-in
provider scripts are the one place it reaches a network service, and only when
the user runs them with their own key. Catalog/discovery,
routing, setup/update, projection and doctor only read/write declared data. Running
an optional script is a separate explicit host action in an approved output folder.
Neither a manifest nor a plan authorizes dependency installation or publication.

Initialization/timeline/local PCM need Python >=3.11. Capture needs explicit local
Node, `puppeteer-core` dependency root, browser executable, FFmpeg with libx264,
and FFprobe; without FFmpeg zscale pass `--color swscale`. numpy (music analysis),
Pillow (image prep, labelled sheets) and macOS `swiftc` (cutouts) are optional. No automatic npm/pip/font download, home/account scan, CDN,
paid music/voice/STT or provider client. Fonts/media are separate licensed inputs.
Capture checks TTF/OTF cmap coverage plus actual font/image readiness; that is not
shaping, accessibility or native-language certification. Text is intact by default,
with per-run language/direction and isolated literal LTR tokens in mixed RTL text.

The explicitly tested capture adapter is Puppeteer on **macOS**, with Chrome
headless-shell 145.0.7632.6 and 152.0.7977.54 and puppeteer-core 25.15.0. The full Chrome headless
surface path stalled on the observed host. Linux/Windows optional render execution
is HOLD/refused; installing/copying inert support data remains cross-platform.
Missing required tools, fonts/glyphs, tracks, bad timelines, page errors, timeout,
corrupt decode and loudness parse failures never become measured zero or success.

Serial capture evaluates exact `i/fps` (plus `--sub` shutter offsets), waits for
deterministic draw/readiness and actual compositor screenshot completion, pipes
frames to FFmpeg at CRF 16 by default, converts sRGB into limited BT.709, then
checks decoded frame count, dimensions, full decode and encoded color tags before
success. `--scale` above 1 supersamples back to the page size. Source/font/tool
inputs are bound to the receipt. No workers, chunking, exists-only resume or
cross-platform byte-equality claim. The loopback server serves the project folder
through an explicit allowlist and realpath/symlink containment, rejects URL/
sibling-prefix/traversal/range/external requests, and closes/reaps on failure/cancel.
Read the toolkit's explicit duration/fps/pixel/output/time/sample policy bounds.

## Craft and independent verdicts

Choose media and techniques for the story, not a mandatory decoration list. Each
segment states medium/palette/type/texture/composition/focal point/motion/cut and
sound intent, tied to sources/rights. Render actual style and moving proofs at
delivery size. Reject repeated headline/card/rail grammar when only text/color
changes. Critique every segment, name strongest/weakest, spend a reserved budget
rebuilding the weakest, compare same-time before/after and both cut boundaries,
then review the final encoded revision.

Keep technical, artistic, full-speed playback, listening, native-language and STT
observations separate. Technical PASS and source inspection cannot fill artistic
or hearing fields; required unobserved checks remain HOLD/`not_observed`. Audio
modes explicitly distinguish muted from missing required tracks; the modest local
score is a fallback, not orchestral/paid-service equivalence. Mix exports stems,
automation and speech-aware ducking. Sample limiting is not true-peak certification;
measure final AAC independently, and do not confuse measurement with normalization.
Generated, attached, received and published are separate states.

## Provenance and maintenance

The companion engine and scripts are MIT material from revfactory/showreel at
`64521220a2f8e91cb9894400c48c15b7f45205d6`, Copyright (c) 2026 Minho Hwang
(revfactory). `toolkit/LICENSE.upstream` is verbatim; per-file notices and
`toolkit/upstream-manifest.json` distinguish upstream files kept below a notice,
modified scripts, original OMH helpers/guidance, the unchanged license and the
translated `craft-*` references. Upstream's provider scripts (`eleven.py`,
`narrate.py`, `codex_ref_image.sh`) ship as opt-in: they reach ElevenLabs or
codex only when the user runs them with their own `ELEVENLABS_API_KEY` or
signed-in codex, refuse with exit 2 otherwise, and are never run by OMH. The provenance inventory excludes its own
self-hash; outer native/portable inventories hash it normally. Keep these notices
in every distribution and reproducible source package; asset/font rights remain
separate. See [Skill sources](SKILL-SOURCES.md).

Maintainer-only regeneration after an audited canonical source change:

```sh
python tools/render_skill_pack.py --write --refresh-provenance
python tools/render_skill_pack.py --check
python -m omh.cli docs agent-skills
python -m omh.cli docs agent-skills --check
```

Refresh a strict editable development environment offline after adding resources
so absolute-path helpers resolve current package data. Regenerate all affected
projections, derive exact budgets/digests from producers and run source-hidden
built wheel/native/local-source/portable parity plus actual dependency-present
host fixtures. A skipped optional test is not observed renderer/audio evidence.
Independent review, CI, PR update and any commissioned film remain separate work.
