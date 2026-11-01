from io import BytesIO
import os
import socket
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import urllib3
from core.ui.product_images import MAX_BYTES, remote_photo


def png():
    out = BytesIO()
    Image.new('RGB', (10, 10), 'white').save(out, format='PNG')
    return out.getvalue()


class ImageTrustBoundaryTests(unittest.TestCase):
    def setUp(self):
        remote_photo.clear()

    def public_dns(self):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('104.18.1.2', 443))]

    def response(self, *, status=200, headers=None, chunks=None):
        response=Mock(status=status, headers=headers or {'Content-Type':'image/png'})
        response.read1.side_effect=[png(),b''] if chunks is None else chunks
        return response

    def fetch(self, response, dns=None, url='https://image.woodongs.com/product.png', clock=None):
        with patch('core.ui.product_images.socket.getaddrinfo',return_value=dns or self.public_dns()) as lookup, \
             patch('core.ui.product_images.urllib3.HTTPSConnectionPool') as pool:
            pool.return_value.urlopen.return_value=response
            if clock:
                with patch('core.ui.product_images.time.monotonic',side_effect=clock):
                    result=remote_photo(url)
            else:
                result=remote_photo(url)
            return result,lookup,pool

    def test_external_image_hostname_cannot_trigger_an_arbitrary_request(self):
        response = Mock(status_code=200, headers={'Content-Type': 'image/png'})
        response.iter_content.return_value = [png()]
        request = Mock()
        request.return_value.__enter__ = Mock(return_value=response)
        request.return_value.__exit__ = Mock(return_value=False)
        with patch('core.ui.product_images.requests.get', request):
            self.assertIsNone(remote_photo('https://attacker-controlled.example/asset.png'))
        request.assert_not_called()

    def test_only_reviewed_https_hosts_and_default_port_are_accepted(self):
        bad_urls=(
            'http://image.woodongs.com/a.png',
            'https://image.woodongs.com:8443/a.png',
            'https://image.woodongs.com:0/a.png',
            'https://image.woodongs.com:bad/a.png',
            'https://user:password@image.woodongs.com/a.png',
            'https://@image.woodongs.com/a.png',
            'https://image.woodongs.com.evil.example/a.png',
            'https://image.woodongs.com./a.png',
            'https://image.woodongs.com/a.png#fragment',
            'https://image.woodongs.com/\nsecret',
            'https://image.woodongs.com\\private/a.png',
            'https://127.0.0.1/a.png',
            'https://[::1]/a.png',
            'https://image.woodongs.com/'+'x'*2049,
        )
        with patch('core.ui.product_images.socket.getaddrinfo') as dns, \
             patch('core.ui.product_images.urllib3.HTTPSConnectionPool') as pool:
            for url in bad_urls:
                with self.subTest(url=url):
                    self.assertIsNone(remote_photo(url))
            dns.assert_not_called()
            pool.assert_not_called()

    def test_private_metadata_link_local_and_multicast_dns_never_connect(self):
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','192.168.1.1','0.0.0.0',
                        '224.0.0.1','::1','fe80::1','fd00::1','::ffff:127.0.0.1','not-an-address'):
            remote_photo.clear()
            with self.subTest(address=address):
                result,_,pool=self.fetch(self.response(),[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))])
                self.assertIsNone(result)
                pool.assert_not_called()

    def test_mixed_public_and_private_dns_answers_are_rejected(self):
        dns=self.public_dns()+[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',443))]
        result,_,pool=self.fetch(self.response(),dns)
        self.assertIsNone(result)
        pool.assert_not_called()

    def test_dns_failure_is_a_missing_photo_not_app_failure(self):
        with patch('core.ui.product_images.socket.getaddrinfo',side_effect=socket.gaierror), \
             patch('core.ui.product_images.urllib3.HTTPSConnectionPool') as pool:
            self.assertIsNone(remote_photo('https://image.woodongs.com/product.png'))
            pool.assert_not_called()

    def test_empty_dns_answers_never_connect(self):
        with patch('core.ui.product_images.socket.getaddrinfo',return_value=[]), \
             patch('core.ui.product_images.urllib3.HTTPSConnectionPool') as pool:
            self.assertIsNone(remote_photo('https://image.woodongs.com/product.png'))
            pool.assert_not_called()

    def test_tls_verification_failure_is_not_retried_or_bypassed(self):
        with patch('core.ui.product_images.socket.getaddrinfo',return_value=self.public_dns()), \
             patch('core.ui.product_images.urllib3.HTTPSConnectionPool') as pool:
            pool.return_value.urlopen.side_effect=urllib3.exceptions.SSLError('certificate verification failed')
            self.assertIsNone(remote_photo('https://image.woodongs.com/product.png'))
            pool.return_value.urlopen.assert_called_once()
            pool.return_value.close.assert_called_once()

    def test_validated_address_is_pinned_and_tls_identity_is_preserved(self):
        response=self.response()
        result,dns,pool=self.fetch(response,url='https://image.woodongs.com:443/product.png?size=large')
        self.assertEqual(Image.open(BytesIO(result)).size,(640,420))
        dns.assert_called_once_with('image.woodongs.com',443,type=socket.SOCK_STREAM)
        self.assertEqual(pool.call_args.args,('104.18.1.2',))
        tls=pool.call_args.kwargs
        self.assertEqual(tls['port'],443)
        self.assertEqual(tls['server_hostname'],'image.woodongs.com')
        self.assertEqual(tls['assert_hostname'],'image.woodongs.com')
        self.assertEqual(tls['cert_reqs'],'CERT_REQUIRED')
        self.assertTrue(tls['ca_certs'])
        self.assertFalse(tls['retries'])
        request=pool.return_value.urlopen.call_args
        self.assertEqual(request.args,('GET','/product.png?size=large'))
        self.assertEqual(request.kwargs['headers']['Host'],'image.woodongs.com')
        self.assertFalse(request.kwargs['redirect'])
        self.assertFalse(request.kwargs['retries'])
        self.assertFalse(request.kwargs['preload_content'])
        self.assertFalse(request.kwargs['decode_content'])
        self.assertFalse(request.kwargs['assert_same_host'])
        response.close.assert_called_once()
        pool.return_value.close.assert_called_once()

    def test_environment_proxy_cookies_and_netrc_are_not_loaded(self):
        with patch.dict(os.environ,{'HTTPS_PROXY':'http://127.0.0.1:9999','ALL_PROXY':'http://127.0.0.1:9999'}), \
             patch('core.ui.product_images.requests.get') as request:
            result,_,pool=self.fetch(self.response())
        self.assertIsNotNone(result)
        request.assert_not_called()
        self.assertNotIn('_proxy',pool.call_args.kwargs)
        headers=pool.return_value.urlopen.call_args.kwargs['headers']
        self.assertNotIn('Authorization',headers)
        self.assertNotIn('Cookie',headers)

    def test_redirect_and_non_images_are_not_followed_or_decoded(self):
        for status,headers in [(302,{'Location':'http://127.0.0.1/secret'}),
                               (200,{'Content-Type':'text/html'}),
                               (200,{'Content-Type':'image/png','Content-Encoding':'gzip'})]:
            remote_photo.clear()
            response=self.response(status=status,headers=headers)
            result,_,pool=self.fetch(response)
            self.assertIsNone(result)
            response.read1.assert_not_called()
            pool.return_value.urlopen.assert_called_once()
            response.close.assert_called_once()

    def test_declared_or_actual_excess_bytes_are_rejected(self):
        for length in (str(MAX_BYTES+1),'-1','invalid'):
            remote_photo.clear()
            response=self.response(headers={'Content-Type':'image/png','Content-Length':length})
            result,_,_=self.fetch(response)
            self.assertIsNone(result)
            response.read1.assert_not_called()
        remote_photo.clear()
        response=self.response(chunks=[b'x'*65536]*49)
        result,_,_=self.fetch(response)
        self.assertIsNone(result)
        self.assertLessEqual(response.read1.call_count,49)

    def test_slow_body_stops_after_budget_and_invalid_images_fall_back(self):
        result,_,_=self.fetch(self.response(chunks=[b'x',b'x']),clock=[0,0,9])
        self.assertIsNone(result)
        remote_photo.clear()
        result,_,_=self.fetch(self.response(chunks=[b'not an image',b'']))
        self.assertIsNone(result)

    def test_checked_in_cu_catalog_and_observed_gs_hosts_remain_supported(self):
        urls=('https://tqklhszfkvzk6518638.edge.naverncp.com/product/8801056193010.png',
              'https://image.woodongs.com/imgsvr/wdlvyItem/BGD_20240123_236992.jpg')
        for url in urls:
            remote_photo.clear()
            result,_,_=self.fetch(self.response(),url=url)
            self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
