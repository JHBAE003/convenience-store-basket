from copy import deepcopy
import unittest
from unittest.mock import Mock

from core.data.live_mcp_adapter import DaisoMcpAdapter


class SecurityCandidateBudgetTests(unittest.TestCase):
    def test_oversized_upstream_page_is_limited_and_marked_partial(self):
        payload={'standard':{'products':[{'code':str(i),'name':f'음료{i}250ml'} for i in range(101)]},
                 'diagnostics':{'source':'모의공개원본'},'X-API-Key':'synthetic-key'}
        original=deepcopy(payload)
        client=Mock()
        client.call.return_value=payload
        adapter=DaisoMcpAdapter('GS25',mcp_client=client)
        rows=adapter.search_products('음료',37.5,127)
        self.assertEqual(len(rows),20)
        self.assertTrue(adapter.catalog_incomplete)
        self.assertEqual(payload,original)
        raw=rows[0].raw_payload['response']
        self.assertEqual(len(raw['standard']['products']),101)
        self.assertNotIn('X-API-Key',raw)
        self.assertTrue(all(row.raw_payload['response'] is raw for row in rows))
        clone=deepcopy(rows)
        clone[0].raw_payload['response']['diagnostics']['source']='다른 복사본'
        self.assertEqual(raw['diagnostics']['source'],'모의공개원본')

    def test_normal_small_page_does_not_become_incomplete(self):
        client=Mock()
        client.call.return_value={'standard':{'products':[{'code':'P','name':'음료250ml'}]}}
        adapter=DaisoMcpAdapter('GS25',mcp_client=client)
        self.assertEqual(len(adapter.search_products('음료',37.5,127)),1)
        self.assertFalse(adapter.catalog_incomplete)
