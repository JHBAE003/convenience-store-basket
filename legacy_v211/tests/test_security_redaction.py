import unittest
from core.data.live_mcp_adapter import DaisoMcpAdapter


class SecurityRedactionTests(unittest.TestCase):
    def test_separator_variants_and_nested_credentials_are_removed(self):
        source={'X-API-Key':'synthetic-key', 'Mcp-Session-Id':'synthetic-session',
                'nested':[{'Authorization':'Basic ZHVtbXk=', 'signEnc':'synthetic-sign',
                           'public_code':'P1','quantity':0}], 'request_count':2}
        cleaned=DaisoMcpAdapter.safe_payload(source)
        self.assertEqual(cleaned,{'nested':[{'public_code':'P1','quantity':0}],'request_count':2})
        self.assertEqual(source['X-API-Key'],'synthetic-key')

    def test_text_headers_and_url_credentials_are_redacted(self):
        for text,secret in [('Authorization: Basic ZHVtbXk6cHc=', 'ZHVtbXk6cHc='),
                            ('GET https://dummy-user:dummy-pass@example.com/path','dummy-pass'),
                            ('Cookie: sid=synthetic-session; pref=1','synthetic-session'),
                            ('https://example.com/?signEnc=synthetic-sign&itemCode=P1','synthetic-sign'),
                            ('session-id=synthetic-session','synthetic-session')]:
            with self.subTest(text=text):
                self.assertNotIn(secret,DaisoMcpAdapter.safe_payload({'message':text})['message'])

    def test_public_diagnostics_source_times_and_zero_are_preserved(self):
        source={'diagnostics':{'status':502,'retryable':True},'stock':0,
                'received_at':'2026-11-01T09:00:00+09:00','source_updated_at':'',
                'url':'https://example.com/image?itemCode=P1','product_name':'펩시600ml'}
        self.assertEqual(DaisoMcpAdapter.safe_payload(source),source)
