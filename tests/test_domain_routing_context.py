from __future__ import annotations

import unittest

from _platform_support import requires_domain_intelligence_store
from domain_context_resolution_support import DomainContextResolverMixin
from domain_routing_context_support import (
    CLAIM_BOUNDARY as CLAIM_BOUNDARY,
    _approve_profile as _approve_profile,
    _binding as _binding,
    _contract_module as _contract_module,
    _repository as _repository,
    _resolver as _resolver,
    _sales_target as _sales_target,
    _store as _store,
)
from test_domain_phrase_matching import DomainPhraseMatcherMixin
from test_domain_routing_contract import DomainRoutingContextContractMixin


class DomainRoutingContextContractTests(
    DomainRoutingContextContractMixin, unittest.TestCase
):
    pass


class DomainPhraseMatcherTests(DomainPhraseMatcherMixin, unittest.TestCase):
    pass


@requires_domain_intelligence_store
class DomainContextResolverTests(DomainContextResolverMixin, unittest.TestCase):
    pass
