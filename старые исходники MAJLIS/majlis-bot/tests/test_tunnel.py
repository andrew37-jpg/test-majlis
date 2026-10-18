import unittest,json
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from tunnel import QuickTunnel,TunnelError
class Response:
 def __init__(self,data):self.data=data
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self):return json.dumps(self.data).encode()
class DiscoveryTests(unittest.TestCase):
 def test_public_service_targets_and_addresses_only(self):
  def request(req,**kwargs):
   name=parse_qs(urlsplit(req.full_url).query)['name'][0]
   if name.startswith('_v2-'):return Response({'Status':0,'Answer':[{'type':33,'data':'1 1 7844 region1.v2.argotunnel.com'},{'type':33,'data':'2 1 22 attacker.example'}]})
   self.assertEqual(name,'region1.v2.argotunnel.com')
   return Response({'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'},{'type':1,'data':'198.41.200.10'},{'type':1,'data':'10.0.0.1'}]})
  with patch('tunnel.urllib.request.urlopen',side_effect=request):self.assertEqual(QuickTunnel(8875,doh_edges=True).edge_addresses(),['198.41.200.10:7844'])
 def test_vpn_hostname_mode_uses_verified_service_names(self):
  response=Response({'Status':0,'Answer':[{'type':33,'data':'1 1 7844 region1.v2.argotunnel.com'}]})
  with patch('tunnel.urllib.request.urlopen',return_value=response) as request:
   self.assertEqual(QuickTunnel(8875,doh_edges=True,edge_hostnames=True).edge_addresses(),['region1.v2.argotunnel.com:7844'])
   self.assertEqual(request.call_count,1)
 def test_failed_lookup_is_not_accepted(self):
  with patch('tunnel.urllib.request.urlopen',return_value=Response({'Status':3})),self.assertRaises(TunnelError):QuickTunnel(8875,doh_edges=True).edge_addresses()
if __name__=='__main__':unittest.main()
