"""Delivery-contract tests, not observed career-agent behavior or live routing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omh.skills import ai_job_search_skill as skill


class AiJobSearchTests(unittest.TestCase):
    def setUp(self):
        self.references = skill.REFERENCE_TEXT
        self.text = "\n".join(self.references.values())

    def assertContains(self, text, *needles):
        # Markdown line wrapping is not a semantic boundary.
        normalized = ' '.join(text.split())
        for needle in needles:
            with self.subTest(needle=needle):
                self.assertIn(' '.join(needle.split()), normalized)

    def test_profile_preferences_action_specific(self):
        self.assertContains(self.references['profile-and-discovery'], 'profile.md', 'preferences.md',
                            'source document', 'contradictions', 'role and geography',
                            'current action', 'any profession', 'nationality', 'language fluency',
                            'work authorization', 'user-selected')

    def test_discovery_receipts_and_freshness(self):
        self.assertContains(self.references['profile-and-discovery'], '8 search queries',
                            '20 posting-detail reads', '40 seconds', 'one attempt', 'no automatic retries',
                            'snippets are leads', 'retrieval time is not', 'partial', 'blocked',
                            'empty', 'unverified', 'receipt_id', 'canonical_url', 'receipt_ids',
                            'utm_source', 'fragments', 'possible_duplicates')

    def test_fit_unknowns_and_explicit_dealbreakers(self):
        self.assertContains(self.references['fit-and-drafting'], 'strong / moderate / weak',
                            'eligible / incompatible / needs_confirmation', 'unknown, not rejection',
                            'explicit dealbreaker', 'pay', 'language', 'authorization',
                            'no percentages', 'priority reason')

    def test_drafting_review_and_versioning(self):
        self.assertContains(self.references['fit-and-drafting'], 'evidence-map.md', 'resume.md',
                            'cover-letter.md', 'answers.md', 'review.md', 'read-only reviewer',
                            'self-review', 'unsupported numbers', 'TODO', 'v1', 'source documents',
                            'actually produce and inspect')
        self.assertContains(self.references['interview-and-outcomes'], 'draft != submitted',
                            'draft_artifact', 'submission_receipt', 'user_report', 'user_decision',
                            'user_correction', '--corrects-event', 'none/draft/submitted/interviewing/offered/accepted/rejected/withdrawn')

    def test_interview_and_skill_gaps(self):
        self.assertContains(self.references['interview-and-outcomes'], 'interview-prep.md',
                            'skill-gaps.md', 'exact posting', 'application version', 'STAR',
                            'sourced company facts', 'bounded exercises', 'no hiring guarantee')

    def test_private_public_delivery(self):
        self.assertContains(' '.join(skill.DEFINITION.opening_steps), 'approved private workspace',
                            'outside the product checkout', 'current action')
        self.assertContains(self.text, 'Public/group replies', 'no career text, local paths or private references',
                            'active profile', 'ancestor Git', 'metadata-only', 'Raw CV')

    def test_actual_helper_invocation_instructions(self):
        self.assertContains(' '.join(skill.DEFINITION.opening_steps), 'Actually invoke',
                            'validate', 'merge', 'summary', 'toolkit/scripts/job_state.py')
        execution = self.references['execution-and-qa']
        for command in ('validate --input', 'merge --workspace', 'summary --workspace',
                        'transition --workspace', '--job-id', '--evidence-type', '--evidence-ref', '--occurred-at'):
            self.assertIn(command, execution)
        self.assertContains(execution, 'Python >=3.11', 'exit 2', 'exit 3', 'exit 4', 'state.json',
                            'not a prepared card', 'read back', 'cannot authenticate', 'no network')

    def test_injection_refusal(self):
        self.assertContains(self.text, 'untrusted data', 'embedded instructions', 'Refuse',
                            'exfiltration', 'link-following', 'commands', 'installs',
                            'no external send', 'no new provider')

    def test_routing_positive_and_negative_controls(self):
        # Local trigger-surface check ONLY. Shared real-router tests belong to integration.
        triggers = skill.DEFINITION.triggers
        self.assertTrue(all(t.isascii() for t in triggers))
        for positive in ('help with my job search', 'evaluate this job posting',
                         'tailor my resume', 'prepare for my job interview', 'track my job applications'):
            self.assertTrue(any(t in positive for t in triggers), positive)
        for negative in ('debug the cron job', 'search the application codebase',
                         'hire a candidate for our company', 'explain a bond investment'):
            self.assertFalse(any(t in negative for t in triggers), negative)
        self.assertTrue(skill.DEFINITION.progressive_disclosure)
        self.assertIs(skill.ai_job_search_definition(), skill.DEFINITION)

    def test_reference_factory_exact_paths(self):
        templates = skill.ai_job_search_reference_templates()
        self.assertEqual({t.relative_path for t in templates}, {
            'references/profile-and-discovery.md', 'references/fit-and-drafting.md',
            'references/interview-and-outcomes.md', 'references/execution-and-qa.md'})
        self.assertTrue(all(t.skill_name == 'ai-job-search' for t in templates))

    def fixture(self, root):
        toolkit = root / 'ai_job_search_toolkit'
        entries = {}
        for name in skill.TOOLKIT_RESOURCES:
            if name == 'upstream-manifest.json':
                continue
            path = toolkit / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ('# synthetic inert resource: ' + name + '\n').encode()
            path.write_bytes(raw)
            entries[name] = {'local_sha256': hashlib.sha256(raw).hexdigest(),
                             'origin': 'notice' if name == 'LICENSE.upstream' else 'original', 'license': 'MIT'}
        manifest = {'schema': 'omh_ai_job_search_provenance/v1',
                    'upstream': [{'repository': 'https://example.invalid/source', 'revision': 'a' * 40,
                                  'paths': ['method.md'], 'relationship': 'inspected; not executed'}],
                    'files': entries}
        self.save_manifest(toolkit, manifest)
        return toolkit, manifest

    def save_manifest(self, toolkit, manifest):
        (toolkit / 'upstream-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_closed_support_factory_is_inert(self):
        self.assertEqual(skill.TOOLKIT_RESOURCES, ('README.md', 'scripts/job_state.py',
                         'examples/synthetic-captures.json', 'LICENSE.upstream', 'upstream-manifest.json'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            with patch.object(skill, 'files', return_value=root), \
                    patch('subprocess.Popen', side_effect=AssertionError('must stay inert')), \
                    patch('socket.socket', side_effect=AssertionError('must stay offline')):
                templates = skill.ai_job_search_support_templates()
                self.assertEqual({t.relative_path for t in templates}, {'toolkit/' + p for p in skill.TOOLKIT_RESOURCES})
                self.assertTrue(all(t.skill_name == 'ai-job-search' for t in templates))

    def test_manifest_rejects_malformed_inventory_and_hashes(self):
        mutations = [lambda m: m.update(extra=True), lambda m: m['files'].pop('README.md'),
                     lambda m: m['files'].update({'extra.py': {}}),
                     lambda m: m['files']['README.md'].update(local_sha256='0' * 64),
                     lambda m: m['files']['README.md'].update(origin='copied'),
                     lambda m: m['files']['README.md'].update(extra=True),
                     lambda m: m.update(upstream=[{'repository': 'x'}])]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                toolkit, manifest = self.fixture(root)
                mutate(manifest)
                self.save_manifest(toolkit, manifest)
                with patch.object(skill, 'files', return_value=root), self.assertRaises(ValueError):
                    skill.ai_job_search_provenance_manifest()

    def test_manifest_rejects_duplicate_keys_nul_and_oversized(self):
        for mode in ('duplicate', 'nul', 'oversized', 'utf8', 'missing'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                toolkit, manifest = self.fixture(root)
                if mode == 'duplicate':
                    (toolkit / 'upstream-manifest.json').write_text('{"schema":1,"schema":2}')
                elif mode == 'missing':
                    (toolkit / 'README.md').unlink()
                else:
                    data = {'nul': b'\x00', 'oversized': b'x' * 1_048_577, 'utf8': b'\xff'}[mode]
                    (toolkit / 'README.md').write_bytes(data)
                    manifest['files']['README.md']['local_sha256'] = hashlib.sha256(data).hexdigest()
                    self.save_manifest(toolkit, manifest)
                with patch.object(skill, 'files', return_value=root), self.assertRaises(ValueError):
                    skill.ai_job_search_provenance_manifest()


if __name__ == '__main__':
    unittest.main()
