import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('hard_repair',Path(__file__).resolve().parents[1]/'deploy'/'hard_repair.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RepairTests(unittest.TestCase):
    def test_remove_only_requested_exact_domain(self):
        text='server { server_name postmaster.justisolve.com sprintforge.netwizzard.tech *.netwizzard.tech; }'
        fixed=r.remove_name(text)
        self.assertNotIn(' sprintforge.netwizzard.tech',fixed)
        self.assertIn('postmaster.justisolve.com',fixed)
        self.assertIn('*.netwizzard.tech',fixed)
    def test_single_domain_replacement_preserves_block(self):
        fixed=r.remove_name('server { server_name "sprintforge.netwizzard.tech"; listen 443 ssl; }')
        self.assertIn('sprintforge-replaced-',fixed)
        self.assertIn('listen 443 ssl;',fixed)
    def test_comments_and_unrelated_domains_unchanged(self):
        text='# server_name sprintforge.netwizzard.tech;\nserver { server_name olamidepanel.com; }'
        self.assertEqual(r.remove_name(text),text)
    def test_include_is_idempotent(self):
        fixed=r.inject('events {} http { include /etc/nginx/conf.d/*.conf; }')
        self.assertEqual(r.inject(fixed),fixed)
        self.assertEqual(fixed.count(str(r.MANAGED)),1)
        with self.assertRaises(RuntimeError):r.inject('events {}')
    def test_renders_http_and_tls(self):
        repair=object.__new__(r.Repair)
        for tls in (False,True):
            text=repair.config(['127.0.0.1','154.16.170.86','::1'],tls)
            self.assertIn('listen 154.16.170.86:80;',text)
            self.assertIn('listen [::1]:80;',text)
            self.assertIn('location ^~ /.well-known/acme-challenge/',text)
            self.assertEqual('listen 154.16.170.86:443 ssl;' in text,tls)
            self.assertEqual(text.count('{'),text.count('}'))

if __name__=='__main__':unittest.main()
